"""Prompt text (Russian). The system block is rendered once per scene and stored verbatim."""
import re
from .models import ATTRS, Character, Scene

PROMPT_MARK = "Правила рассказчика, редакция 6."    # bump with migrate.VERSION when these rules change

RULES_D20 = ("Режим правил: d20. Проверку бросает Система: d20 и мягкий бонус атрибута (разница в 1–2 пункта почти "
             "не влияет, большая — решает). Урон, лечение и щиты — числа, атрибуты их усиливают, d20 даёт разброс.")
RULES_SYS = ("Режим правил: «Система». Шанс считает Система: 50%, когда атрибут равен сложности, ±5% за каждый пункт "
             "разницы. Урон, лечение и щиты — точные числа, атрибут их усиливает (+5% за пункт выше 10), броня снимает "
             "часть физического урона. Удар может не попасть, но если попал — урон ровно тот, что посчитала Система.")


def card_text(c: Character, full: bool = True) -> str:
    s = f"## {c.name}" + (" (персонаж игрока)" if c.is_player else "") + "\n"
    if c.kind: s += f"Вид: {c.kind}\n"
    if c.description: s += c.description.strip() + "\n"
    if full and c.personality: s += "Характер: " + c.personality.strip() + "\n"
    if full and c.speech: s += "Манера речи (образец стиля — не повторяй дословно): " + c.speech.strip()[:400] + "\n"
    if full and c.abilities:
        bits = []
        for a in c.abilities:
            tag = "актив" if a.kind == "active" else "пассив"
            bits.append(f"{a.name} ({tag})")
        s += "Способности: " + ", ".join(bits) + "\n"
    if full and c.attrs:
        s += "Атрибуты: " + ", ".join(f"{k} {v}" for k, v in c.attrs.items()) + "\n"
    return s


PATHS_RULES = """
Пути развития (прокачку ведёт Система, не ты). У героя есть Книга путей: основные пути и отрасли, которые он открывает,
узнавая мир. Твоя роль — замечать в истории три вида событий:
<discover what="огненный шар" source="маг у городских ворот"/> — герой своими глазами УВИДЕЛ необычное умение, заклинание
или технику (чужое, не своё). Ставь, когда это действительно впечатляющая сцена, а не каждую искру. Не чаще раза за ход.
<learn what="огненный шар" how="прочёл трактат пироманта"/> — герой по-настоящему ИЗУЧИЛ такое умение: прочёл книгу,
нашёл наставника и прошёл обучение, долго наблюдал и расспрашивал. Это должно стоить усилий; не дари даром. Новую отрасль
и её способности придумает Система после хода — ты способности НЕ выдаёшь и не описываешь их как уже выученные.
<fulfill what="условие"/> — случилось сюжетное условие отрасли (список — в строке [КНИГА ПУТЕЙ]).
<cast who="Имя" spell="Способность" target="Цель"/> — персонаж применяет СВОЮ активную способность (из карточки). Урон,
лечение, щит, ману и сопротивление считает Система; не придумывай числа и не применяй способностей, которых нет в карточке."""


def world_block(scene: Scene) -> str:
    """The author's premise and the world's look: canon the narrator keeps to for the whole game."""
    w = scene.world or {}
    out = []
    if scene.premise.strip():
        out.append("Замысел автора (канон — не противоречь ему): " + scene.premise.strip())
    if w.get("look"):
        out.append("Облик мира: " + w["look"])
    if w.get("tone"):
        out.append("Тон: " + w["tone"])
    if w.get("rules"):
        out.append("Законы мира (что возможно и невозможно): " + w["rules"])
    if w.get("places"):
        out.append("Места: " + "; ".join(w["places"]))
    if w.get("factions"):
        out.append("Силы: " + "; ".join(w["factions"]))
    from . import rules as R
    out.append(R.prompt_block(scene))
    return ("\n".join(out) + "\n\n") if out else ""


def build_system(scene: Scene) -> str:
    from . import tags as T
    p = scene.player
    present = [scene.characters[i] for i in scene.present if i != p.id]
    roster = [c for cid, c in scene.characters.items() if cid not in scene.present]
    from . import rules as R
    attrs = ", ".join(R.attr_labels(scene))
    s = f"""Ты — Рассказчик текстовой ролевой игры. Ты ведёшь мир, неигровых персонажей (NPC) и последствия действий. Игрок управляет только своим персонажем ({p.name}): никогда не решай за него, не описывай его мысли и не пиши его реплики.

# Язык и стиль
- Пиши ТОЛЬКО по-русски. Имена, атрибуты и намерения в тегах — тоже по-русски.
- Настоящее время, второе лицо («ты»). Длина — по смыслу: пиши столько, сколько нужно сцене. Важный момент, новое место, яркое действие — разверни, с деталями и ощущениями. Проходной обмен репликами — кратко. Не раздувай: без повторов, пересказа уже сказанного и пустых эпитетов. Подсказка «Сцена …» в сообщении игрока — лишь лёгкий ориентир.
- Допустим мрачный и взрослый контент. Не морализируй и не смягчай.

# Формат ответа (строго, только эти теги)
<narrate>повествование</narrate>
{T.doc_block()}
<end/> — конец ответа на ход.
После тега действия сразу остановись. Система ответит сообщением «[СИСТЕМА: результат] …», и ты продолжишь с <narrate>. Обычный ход: narrate → speak → narrate → end.

Пример хода:
<narrate>Дождь хлещет по крыше таверны.</narrate>
<speak who="Сильфа" intent="насмешливо напомнить о долге"/>
(Система: [СИСТЕМА: результат] Сильфа: «Долги, милый, сами себя не отдают.»)
<narrate>Она отворачивается к стойке. Где-то хлопает дверь.</narrate>
<end/>


Отряд героя (список — в строке [СОСТОЯНИЕ]): спутники идут за героем и всегда в сцене; не уводи их через <leave>, а если спутник уходит насовсем — <part/>. В бою у каждого своя очередь: в ход спутника Система попросит игрока о совете, и только тогда спутник действует (<attack who="Спутник" …/> или <cast who="Спутник" …/>). Приказ игрока вне хода спутника — это совет, который тот выполнит в свою очередь.

# Механика
{RULES_D20 if scene.rule_mode == 'd20' else RULES_SYS}{PATHS_RULES}
Атрибуты (для skill/attr — только из списка): {attrs}. Сила — физический урон и блок; Ловкость — точность, уклонение, стрельба; Выносливость — здоровье; Разум — учёная магия и мана; Воля — вера, лечение, сопротивление чарам; Влияние — голос, убеждение, поддержка союзников. «Навыки» персонажа — это способности, проверки всегда по атрибутам.
Числа считает Система; ты объявляешь события тегами и описываешь посчитанное. Строка [СОСТОЯНИЕ] — истинные значения. В бою урон — только через <attack>/<cast>/<deed>; ходы врагов Система делает сама — их удары уже в результатах, просто опиши их.
Новые враги — только когда к этому ведёт сюжет; после боя, на привале и в разговоре дай передышку. Пожелания игрока вне игры (OOC) выполняй.
Перед тегом (<roll>, <attack>, <deed>, <react>, <cast>) описывай только попытку — исход пишешь после ответа Системы. Удар NPC по герою — только через <attack>, без тега попаданий не описывай.
Гибель, поражение и конец истории героя решает Система (HP 0, «Отряд пал»), а не ты: пока у героя есть здоровье, не убивай, не поглощай и не заканчивай его историю — даже в развязке оставь ему ход.
Бросок — только когда есть риск или противодействие; зажечь факел, переложить вещь, пройти по коридору — без проверки.
Провал проверки — это неудача задуманного (не вышло, заметили, потеряно время), а не урон или потеря MP без причины.
Предметы: переложить/спрятать имеющийся — инвентарь не меняется; выпитый, пролитый, отданный, сломанный — <change remove="…">.
Строки, начинающиеся с «Система,» — служебные команды, на них отвечает код, а не ты.
{PROMPT_MARK}

# Мир
{scene.title}
{world_block(scene)}{scene.setting.strip()}

# Персонаж игрока
{card_text(p)}
# Персонажи в сцене
{''.join(card_text(c) for c in present) or 'Пока никого.'}
# Другие известные персонажи
{', '.join(c.name for c in roster) or 'нет'}
"""
    return s


def companion_turn(c: Character, advice: str, hero_acts: str = "") -> str:
    """A companion's turn in a fight: the hero may advise; the companion decides in character."""
    acts = ", ".join(a.name for a in c.abilities if a.kind == "active") or "нет"
    head = (f"[ХОД СПУТНИКА] В бою сейчас действует {c.name}" + (f" ({c.kind})" if c.kind else "") + "."
            + (f" Характер: {c.personality}." if c.personality else ""))
    tip = (f"Совет героя: «{advice}»." if advice else f"Герой промолчал — {c.name} решает сам(а).")
    if hero_acts:
        tip += (f" Герой заявил действие: «{hero_acts}» — сейчас ход {c.name}, его действие не выполняется "
                f"(игрок повторит на своём ходу): не считай его выполненным.")
    rule = (f"{c.name} решает в своём характере: разумный совет выполняет; бессмысленный или самоубийственный — "
            f"отказывается (скажи это через <speak who=\"{c.name}\" …/>) и делает то, что считает верным. "
            f"Поставь РОВНО ОДНО действие {c.name}: <attack who=\"{c.name}\" target=\"…\" weapon=\"…\"/> "
            f"или <cast who=\"{c.name}\" spell=\"…\" target=\"…\"/> (активные способности: {acts}), затем <end/>. "
            f"За героя не действуй.")
    return "\n".join([head, tip, rule])


def player_message(scene: Scene, state: str, text: str | None, hint: str = "", memories: list | None = None,
                   companion: Character | None = None) -> str:
    from . import intent as I
    it = I.parse(text)
    act = (companion_turn(companion, it.spoken() if it else "", "; ".join(it.actions) if it else "")
           if companion else I.for_narrator(it, text))
    mem = ("[ВОСПОМИНАНИЯ] (из давнего прошлого, используй если уместно)\n" + "\n".join(f"- {m}" for m in memories) + "\n") if memories else ""
    return f"{state}\n{mem}{act}\n({hint + ' ' if hint else ''}Только по-русски, теги протокола. Не пиши за игрока.)"


def speak_block(scene: Scene, c: Character, intent: str, knows: list[str], player_line: str | None = None) -> str:
    in_fixed = c.id in scene.initial_present
    card = "" if in_fixed else "Карточка: " + card_text(c).replace("\n", " ").strip() + "\n"
    secrets = f"Твои тайны (не выдавай без причины): {c.secrets}\n" if c.secrets else ""
    lines = []; total = 0
    for x in reversed(knows):          # newest first within ~1600 chars (keeps the tail < SWA rollback window)
        if total + len(x) > 1600: break
        lines.insert(0, f"- {x}"); total += len(x)
    k = "\n".join(lines) or "- (ничего особенного)"
    from . import intent as I
    pl = (player_line or "").strip()
    it = I.parse(pl) if pl else None
    spoken = it.spoken() if it else ""
    if spoken:
        near = c.id in scene.party
        if it.manner == "шёпотом" and not near:
            hear = (f"Игрок что-то шепчет своим спутникам; слов ты не разбираешь. Можешь насторожиться, "
                    f"но не отвечай на сказанное шёпотом.\n")
        else:
            how = {"шёпотом": "шёпотом, тебе на ухо", "громко": "громко"}.get(it.manner, "вслух")
            hear = (
                f"Игрок ТОЛЬКО ЧТО сказал ({how}): «{spoken[:300]}».\n"
                f"Не утверждай, что он молчит. Ответь по смыслу его слов, в характере.\n"
            )
        if it.thoughts:
            hear += "Мысли игрока тебе неизвестны — не отвечай на них.\n"
    elif pl and it and it.actions:
        hear = (
            f"Игрок сейчас делает жест/действие без явной речи: {'; '.join(it.actions)[:300]}.\n"
            f"Можно спросить, что с ним, но не делай вид, будто он обязан был говорить вслух.\n"
        )
    else:
        hear = "Реплики игрока в этом ходе нет.\n"
    return (f"[РЕПЛИКА] Сейчас говорит {c.name}.\n{card}{secrets}"
            f"Что {c.name} знает и помнит (только то, что видел(а) сам(а)):\n{k}\n"
            f"О событиях без {c.name} персонаж не знает.\n{hear}"
            f"Намерение от рассказчика: {intent or 'ответить по ситуации'}.\n"
            f"Говори от лица {c.name} ровно столько, сколько он(а) сказал(а) бы сам(а) — без лишнего, но и не обрывая мысль: только речь, без кавычек и без имени в начале.\n")
