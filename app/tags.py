"""The tag protocol in one place: every tag the narrator may use — its required attributes, the line that teaches it
to the model, and the engine method that carries it out (TurnEngine._tag_<handler>). protocol.py parses by this
registry, prompts.py builds the system prompt from it. A new mechanic = one entry here + one method in the engine."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TagSpec:
    name: str
    required: tuple[str, ...] = ()
    doc: str = ""            # the line in the system prompt; "" — taught elsewhere (Книга путей) or not at all
    handler: str = ""        # TurnEngine._tag_<handler>; "" — handled by the turn loop itself (end, scale)
    turn_gated: bool = False  # in a fight only on the actor's turn, and never for an idle hero


TAGS: dict[str, TagSpec] = {t.name: t for t in [
    TagSpec('speak', ('who',), '<speak who="Имя" intent="что персонаж хочет сказать, кратко"/> — персонаж говорит. Реплику напишет сам персонаж, ты задаёшь только намерение. Не пиши прямую речь NPC внутри narrate. Если в [ИГРОК] есть слова (не только *жест*), в intent для <speak> отвечай на них и не ставь «молчит/не отвечает».',
            'speak', False),
    TagSpec('roll', ('skill',), '<roll skill="Атрибут" difficulty="легко|обычно|трудно|очень трудно"/> — проверка атрибута героя, когда есть риск или противодействие.',
            'roll', True),
    TagSpec('reveal', ('who',), '<reveal who="Имя" what="kind|attrs|abilities|all"/> — игрок что-то узнал об NPC (осмотр/расследование).',
            'reveal', False),
    TagSpec('change', ('who',), '<change who="Имя" hp="-5" mp="+3" add="предмет" remove="предмет"/> — вне боя: рана или отдых, находка или потеря. Указывай только нужное. Опыт начисляет Система сама.',
            'change', False),
    TagSpec('introduce', ('name',), '<introduce name="Имя" desc="внешность, характер, роль"/> — в сцену входит новый персонаж. Противнику укажи угрозу: threat="слабый|обычный|опасный|элита|босс" (по силе в мире, Система задаст ему характеристики под уровень героя). Для противников добавь side="враг"; несколько одинаковых — count="3" (Система создаст «Имя 1», «Имя 2», «Имя 3», и дальше обращайся к ним по этим именам).',
            'introduce', False),
    TagSpec('leave', ('who',), '<leave who="Имя"/> — персонаж уходит из сцены.',
            'leave', False),
    TagSpec('join', ('who',), '<join who="Имя"/> — персонаж СОГЛАШАЕТСЯ вступить в отряд героя. Ставь только если он сам этого хочет (по характеру, выгоде, долгу); отказать — нормально, тогда просто покажи отказ через speak. В отряде не больше четырёх вместе с героем — лимит проверит Система.',
            'join_part', False),
    TagSpec('part', ('who',), '<part who="Имя"/> — спутник покидает отряд (обида, предательство, своя дорога).',
            'join_part', False),
    TagSpec('attack', ('target',), '<attack who="Имя" target="Цель" weapon="оружие"/> — атака в бою (числа считает Система).',
            'attack', True),
    TagSpec('deed', ('attr',), '<deed attr="Атрибут" difficulty="легко|обычно|трудно|очень трудно|невозможно" effect="урон|оглушение|ослабление|усиление" target="Цель" how="кратко"/> — боевой приём героя (финт, песок в глаза, удар щитом, опрокинуть жаровню). Ты оцениваешь, реально ли это и насколько трудно; Система бросает и применяет эффект. Вне боя для риска без эффекта — <roll>.',
            'deed', False),
    TagSpec('react', ('attr',), '<react attr="Атрибут" difficulty="легко|обычно|трудно|очень трудно|невозможно" outcome="уклон|смягчить|контратака" how="кратко"/> — герой своими словами защищается от удара, который ещё не нанесён; ставь только когда об этом просит Система. уклон — удар мимо целиком; смягчить — урон вдвое меньше; контратака — без урона и ответный удар (трудно); если игрок вдобавок бьёт или пинает — это outcome="контратака", отдельного <deed> в этом ходу нет. Если игрок вместо защиты атакует (метает нож, бьёт навстречу) — outcome="размен": удар врага проходит, герой бьёт в ответ. Если игрок занят другим и не защищается — outcome="нет". Не придумывай защиту, которой игрок не описал. Сначала <react>, потом описывай исход.',
            'react', False),
    TagSpec('escape', (), '<escape kind="бегство|сдача" attr="Атрибут" difficulty="легко|обычно|трудно|очень трудно|невозможно" how="кратко"/> — герой пытается убежать из боя или сдаться (только если игрок так написал). Реально ли бегство и насколько трудно — решаешь ты, бросает Система; сдача бой заканчивает.',
            'escape', False),
    TagSpec('combat', ('action',), '<combat action="start" foes="Имя1,Имя2"/> / <combat action="end"/> — начать или закончить бой.',
            'combat', False),
    TagSpec('condition', ('who', 'name'), '<condition who="Имя" name="кровотечение" turns="2" potency="2"/> — состояние.',
            'condition', False),
    TagSpec('end', (), '',
            '', False),
    TagSpec('scale', ('level',), '',
            '', False),
    TagSpec('fulfill', ('what',), '',
            'fulfill', False),
    TagSpec('discover', ('what',), '',
            'discover_learn', False),
    TagSpec('learn', ('what',), '',
            'discover_learn', False),
    TagSpec('cast', ('spell',), '',
            'cast', True),
]}


def doc_block() -> str:
    """The tag lines of the system prompt, in registry order."""
    return "\n".join(t.doc for t in TAGS.values() if t.doc)
