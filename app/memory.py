"""Long-term memory: CPU embeddings (multilingual-e5-small ONNX), ChromaDB per scene, witness filters,
layered summaries and facts. Ideas from Talemate's memory agent / layered_history, reimplemented."""
import asyncio, re, threading
from . import config
from .models import Fact, Scene, Summary

_lock = threading.Lock()


class Embedder:
    """multilingual-e5-small (quantized ONNX) with mean pooling; ~120 MB, CPU only."""
    def __init__(self, path=None):
        self.path = path or config.EMBED_DIR
        self._sess = None

    def _load(self):
        if self._sess is None:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            so = ort.SessionOptions(); so.intra_op_num_threads = 2
            self._sess = ort.InferenceSession(str(self.path / "model_quantized.onnx"), so, providers=["CPUExecutionProvider"])
            self._tok = Tokenizer.from_file(str(self.path / "tokenizer.json"))
            self._tok.enable_truncation(256); self._tok.enable_padding()
            self._inputs = {i.name for i in self._sess.get_inputs()}

    def embed(self, texts: list[str], kind: str = "passage") -> list[list[float]]:
        import numpy as np
        with _lock:
            self._load()
            enc = self._tok.encode_batch([f"{kind}: {t}" for t in texts])
            ids = np.array([e.ids for e in enc], dtype=np.int64); mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            out = self._sess.run(None, feed)[0]
            m = mask[..., None].astype(np.float32)
            v = (out * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            v /= np.linalg.norm(v, axis=1, keepdims=True)
            return v.tolist()


class VectorStore:
    """One Chroma collection per scene under data/chroma. Witnesses as boolean metadata w_<id>."""
    def __init__(self, embedder: Embedder, path=None):
        import chromadb
        self.emb = embedder
        self.client = chromadb.PersistentClient(path=str(path or (config.DATA / "chroma")))

    def coll(self, scene_id: str):
        name = "s_" + re.sub(r"[^a-zA-Z0-9_-]", "_", scene_id)[:56]
        return self.client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})

    def add(self, scene_id: str, docs: list[dict]):
        """docs: {id, text, turn, kind, witnesses, who?}"""
        if not docs:
            return
        vecs = self.emb.embed([d["text"] for d in docs])
        metas = []
        for d in docs:
            m = {"turn": int(d["turn"]), "kind": d["kind"], "who": d.get("who") or ""}
            for w in d.get("witnesses", []):
                m["w_" + w] = True
            metas.append(m)
        self.coll(scene_id).upsert(ids=[d["id"] for d in docs], documents=[d["text"] for d in docs],
                                   embeddings=vecs, metadatas=metas)

    def query(self, scene_id: str, text: str, k: int, witness: str | None = None, before_turn: int | None = None,
              kinds: list[str] | None = None, exclude: set | None = None, max_dist: float = 0.25) -> list[dict]:
        conds = []
        if witness: conds.append({"w_" + witness: True})
        if before_turn is not None: conds.append({"turn": {"$lt": int(before_turn)}})
        if kinds: conds.append({"kind": {"$in": kinds}})
        where = None if not conds else conds[0] if len(conds) == 1 else {"$and": conds}
        c = self.coll(scene_id)
        if c.count() == 0:
            return []
        try:
            r = c.query(query_embeddings=self.emb.embed([text], "query"), n_results=min(c.count(), k * 3), where=where)
        except Exception:
            return []
        out = []
        for i, doc, meta, dist in zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0]):
            if exclude and i in exclude:
                continue
            if dist > max_dist:
                continue
            out.append({"id": i, "text": doc, "turn": meta["turn"], "kind": meta["kind"], "dist": round(dist, 3)})
            if len(out) >= k:
                break
        return out

    def delete_from(self, scene_id: str, turn: int):
        self.coll(scene_id).delete(where={"turn": {"$gte": int(turn)}})

    def drop(self, scene_id: str):
        try: self.client.delete_collection(self.coll(scene_id).name)
        except Exception: pass


# ---------------- prompt pieces ----------------
def memory_block(scene: Scene) -> str | None:
    """Rendered as the first user turn after the system prompt. Changes only on archive."""
    if not scene.summaries and not scene.facts:
        return None
    parts = ["[ПАМЯТЬ] Сводка прошлых событий (старое → новое):"]
    for s in sorted(scene.summaries, key=lambda s: (s.from_turn, -s.layer)):
        parts.append(f"- Ходы {s.from_turn}–{s.to_turn}: {s.text}")
    if scene.facts:
        parts.append("Факты:")
        for f in sorted(scene.facts, key=lambda f: f.id):
            who = ", ".join(scene.characters[c].name for c in f.known_by if c in scene.characters)
            parts.append(f"- #{f.id} {f.text}" + (f" (знают: {who})" if who else ""))
    return "\n".join(parts)


def character_knowledge(scene: Scene, cid: str, limit_chars: int = 900) -> list[str]:
    """Only the summaries and facts this character witnessed."""
    out = []
    for s in sorted(scene.summaries, key=lambda s: s.from_turn):
        if cid in s.witnesses:
            out.append(f"(давно, ходы {s.from_turn}–{s.to_turn}) {s.text}")
    for f in scene.facts:
        if cid in f.known_by:
            out.append(f"(факт) {f.text}")
    total = 0; res = []
    for x in reversed(out):  # keep the newest within the char budget
        if total + len(x) > limit_chars:
            break
        res.insert(0, x); total += len(x)
    return res


SUMMARY_SCHEMA = {
    "type": "object", "required": ["summary", "facts"],
    "properties": {
        "summary": {"type": "string", "minLength": 40, "maxLength": 900},
        "facts": {"type": "array", "maxItems": 6, "items": {
            "type": "object", "required": ["text", "known_by"],
            "properties": {"text": {"type": "string", "minLength": 5, "maxLength": 200},
                           "known_by": {"type": "array", "maxItems": 8, "items": {"type": "string"}}}}}}}
COMPRESS_SCHEMA = {"type": "object", "required": ["summary"],
                   "properties": {"summary": {"type": "string", "minLength": 40, "maxLength": 1200}}}


def archiver_request(scene: Scene, from_turn: int, to_turn: int, anchors: dict | None = None) -> str:
    names = ", ".join(c.name for c in scene.characters.values())
    a = anchors or {}
    span = (f"Начало: «[СОСТОЯНИЕ] Ход {from_turn}» (игрок: «{a.get(from_turn, '…')}»). Конец: всё до «[СОСТОЯНИЕ] Ход {to_turn + 1}»"
            + (f" (игрок: «{a[to_turn + 1]}»)" if to_turn + 1 in a else "") + ".")
    return (f"[АРХИВАТОР] Служебная задача, не часть игры. Сожми ТОЛЬКО события ходов {from_turn}–{to_turn}. {span} "
            f"Не пересказывай то, что уже есть в блоке [ПАМЯТЬ], и не включай события после хода {to_turn}. "
            f"Сводка на 60–120 слов по-русски: кто, где, что сделал, чем кончилось, важные имена, места, предметы, обещания, тайны. "
            f"Выпиши также до 5 НОВЫХ конкретных фактов из этих ходов (пароли, долги, имена, что и где игрок спрятал, получил или потерял, кто кому что сказал; "
            f"не повторяй уже известные факты) и для каждого — кто из персонажей это знает (имена из списка: {names}). "
            f"Ответ — только JSON: "
            '{"summary": "...", "facts": [{"text": "...", "known_by": ["Имя"]}]}')


def compress_request(items: list[Summary]) -> str:
    body = "\n".join(f"- Ходы {s.from_turn}–{s.to_turn}: {s.text}" for s in items)
    return (f"[АРХИВАТОР] Служебная задача. Объедини эти сводки в одну на 120–200 слов по-русски, сохранив важные имена, "
            f"факты, долги и тайны:\n{body}\nОтвет — только JSON: {{\"summary\": \"...\"}}")


def add_facts(scene: Scene, facts: list[dict], default_witnesses: list[str], turn: int, resolve) -> list[Fact]:
    import difflib
    new = []
    have = {re.sub(r"\W+", " ", f.text.lower()).strip() for f in scene.facts}

    def dup(key):
        return key in have or any(difflib.SequenceMatcher(None, key, h).ratio() > 0.72 for h in have)
    nid = max([f.id for f in scene.facts], default=0)
    for f in facts[:6]:
        t = (f.get("text") or "").strip()
        key = re.sub(r"\W+", " ", t.lower()).strip()
        if len(t) < 5 or dup(key):
            continue
        kb = []
        for n in f.get("known_by") or []:
            c = resolve(n)
            if c and c.id not in kb:
                kb.append(c.id)
        kb = [c for c in kb if c in default_witnesses] or list(default_witnesses)
        nid += 1
        fact = Fact(id=nid, text=t[:200], known_by=kb, turn=turn)
        scene.facts.append(fact); new.append(fact); have.add(key)
    return new
