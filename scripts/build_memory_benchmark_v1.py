"""Materialize 100 authored scenarios; never pad dialogue to meet quotas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loveapp.evaluation.memory_benchmark_v1 import MemoryBenchmarkCase

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals/memory/benchmark_v1.jsonl"
SCHEMA = ROOT / "evals/memory/benchmark_v1.schema.json"


def claim(
    turn: int,
    kind: str,
    predicate: str,
    values: list[str],
    *,
    subject: str = "user",
    event: str | None = None,
    semantic_type: str = "new_memory",
    attribute: str | None = None,
    policy: str = "semantic",
    span: str | None = None,
    perspective: str | None = None,
) -> dict[str, Any]:
    return dict(
        source_turn_id=f"t{turn}",
        kind=kind,
        predicate=predicate,
        subject=subject,
        event_type=event,
        semantic_type=semantic_type,
        predicate_policy=policy,
        attribute=attribute,
        value_groups=[values],
        span=span,
        perspective=perspective,
    )


def event(turn: int, subtype: str, values: list[str], **kwargs: Any) -> dict[str, Any]:
    return claim(
        turn,
        "interaction_event",
        f"event.{subtype}",
        values,
        event=subtype,
        subject="relationship",
        **kwargs,
    )


def step(
    turn: int,
    operation: str,
    *,
    target: str | None = None,
    refs: list[str] | None = None,
    fields: dict[str, list[str]] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return dict(
        turn_id=f"t{turn}",
        operation=operation,
        target=dict(selector="unique_matching" if target else "none", ref=target),
        claim_refs=refs or [],
        fields=[dict(path=key, alternatives=value) for key, value in (fields or {}).items()],
        **kwargs,
    )


def case(
    number: int,
    category: str,
    scenario: str,
    texts: list[str],
    claims: list[dict[str, Any]],
    steps: list[dict[str, Any]] | None = None,
    *,
    operation: str = "CREATE",
    questions: dict[int, str] | None = None,
    negative: list[int] | None = None,
    review: str | None = None,
) -> dict[str, Any]:
    conversation = []
    for index, text in enumerate(texts, 1):
        if questions and index in questions:
            conversation.append(
                dict(turn_id=f"t{100 + index}", role="assistant", content=questions[index])
            )
        conversation.append(dict(turn_id=f"t{index}", role="user", content=text))
    units = []
    normalized_claims = []
    for index, source in enumerate(claims, 1):
        row = dict(source)
        text = texts[int(row["source_turn_id"][1:]) - 1]
        span = row.pop("span") or text
        row.update(claim_id=f"c{index}", evidence_spans=[span], semantic_target=span)
        normalized_claims.append(row)
        units.append(
            dict(
                turn_id=row["source_turn_id"],
                evidence_span=span,
                semantic_role={
                    "new_memory": "new_proposition",
                    "enrichment": "attribute_completion",
                    "refinement": "refinement",
                }[row["semantic_type"]],
            )
        )
    if steps is None:
        steps = [
            step(int(row["source_turn_id"][1:]), "CREATE", refs=[row["claim_id"]])
            for row in normalized_claims
        ]
    length = "short" if len(texts) <= 3 else "medium" if len(texts) <= 10 else "long"
    return dict(
        id=f"BM-{number:03d}",
        schema_version="memory-benchmark-v1",
        category=category,
        scenario=scenario,
        failure_mode=f"{scenario}：遗漏证据、错选主体/目标或错误演化",
        difficulty="hard" if length == "long" else "medium" if length == "medium" else "easy",
        length_class=length,
        conversation=conversation,
        expected=dict(
            stage1=dict(
                should_extract=bool(claims),
                semantic_units=units,
                negative_turn_ids=[f"t{n}" for n in (negative or [])],
            ),
            stage2=dict(claims=normalized_claims),
            operation=operation,
            sub_operations=steps,
        ),
        review_reason=review,
        notes=("只评分显式 checkpoint；所有用户轮都真实回放。"
               "target.ref 是本 case claim_id，不是 DB id。"),
    )


# Shared, authored distractors deliberately test retention across topic switches.
# They are genuine additional facts/preferences, not length-only acknowledgements.
DISTRACTORS = [
    "我平时坐地铁去上班。",
    "她喜欢吃清淡的粤菜。",
    "我家里养了一只叫团团的猫。",
    "我的生日是五月十二号。",
    "她有一个正在读大学的妹妹。",
    "我不喜欢太吵的餐厅。",
    "我周末的爱好是拍建筑照片。",
    "她的职业是护士。",
    "我会说中文和英语。",
    "她对花生过敏，订餐得注意。",
    "我喜欢科幻电影。",
    "她更喜欢坐靠窗的位置。",
]


def long_dialogue(story: list[str]) -> list[str]:
    assert len(story) == 8
    return story[:3] + DISTRACTORS[:6] + story[3:5] + DISTRACTORS[6:] + story[5:]


def build_cases() -> list[dict[str, Any]]:
    rows = []
    stable = [
        ("姓名按原文保留", ["我叫小林。"], "profile.identity", ["小林"]),
        (
            "职业事实",
            ["我现在是软件工程师。"],
            "profile.occupation",
            ["软件工程师", "software engineer"],
        ),
        (
            "居住地迁移",
            ["我住上海。", "从九月开始我搬到杭州长期住了，不再住上海。"],
            "profile.residence",
            ["上海"],
        ),
        (
            "家庭成员的主体",
            ["我有一个妹妹。", "我妹妹今年上大学了。"],
            "has_sister",
            ["妹妹", "younger sister"],
        ),
        (
            "生日明确纠错",
            ["我的生日是五月十二日。", "我刚才说错了，我的生日是五月二十一日。"],
            "profile.birthday",
            ["五月十二", "05-12", "5月12"],
        ),
        (
            "博士学年细化",
            ["我目前在读博士。", "我已经博士三年级了。"],
            "education",
            ["博士", "phd"],
        ),
        ("宠物事实", ["我养了一只猫。"], "owns_pet", ["猫", "cat"]),
        ("宠物名字", ["我的猫叫团团。"], "pet_name", ["团团"]),
        ("籍贯不等于居住地", ["我老家是杭州，但现在长期住上海。"], "hometown", ["杭州"]),
        (
            "教师岗位细化",
            ["我的职业是老师。", "具体来说我教高中数学。"],
            "profile.occupation",
            ["老师", "教师", "teacher"],
        ),
        ("驾驶资格", ["我已经拿到驾照了。"], "driving_license", ["驾照", "license"]),
        ("稳定通勤方式", ["我平时坐地铁上班。"], "commute", ["地铁", "subway"]),
        ("同住成员", ["我和父母住在一起。"], "household", ["父母", "parents"]),
        ("年龄", ["我今年二十八岁。"], "age", ["二十八", "28"]),
        ("雇主类型", ["我在一家创业公司工作。"], "employer_type", ["创业", "startup"]),
        ("语言技能", ["我会说中文和英语。"], "languages", ["英语", "english"]),
        ("兄弟姐妹数量", ["我有两个哥哥。"], "siblings", ["两个哥哥", "two older brothers"]),
        (
            "联系方式",
            ["我的微信号是 benchmark_xiaolin，不是手机号。"],
            "profile.contact_method",
            ["benchmark_xiaolin"],
        ),
        ("稳定事实重复", ["我住上海。", "我现在仍然住在上海。"], "profile.residence", ["上海"]),
        ("闲聊不保存", ["今天吃什么？"], "none", []),
    ]
    for n, (name, texts, pred, values) in enumerate(stable, 1):
        if n == 20:
            rows.append(
                case(
                    n,
                    "stable_fact",
                    name,
                    texts,
                    [],
                    [step(1, "NOOP")],
                    operation="NOOP",
                    negative=[1],
                )
            )
            continue
        claims = [claim(1, "stable_fact", pred, values)]
        operations = None
        op = "CREATE"
        if n in {3, 5}:
            claims.append(
                claim(
                    2, "stable_fact", pred, ["杭州"] if n == 3 else ["五月二十一", "05-21", "5月21"]
                )
            )
            operations = [
                step(1, "CREATE", refs=["c1"]),
                step(2, "UPDATE", target="c1", refs=["c2"]),
            ]
            op = "UPDATE"
        elif n == 19:
            operations = [step(1, "CREATE", refs=["c1"]), step(2, "MERGE", target="c1")]
            op = "MERGE"
        elif n in {6, 10}:
            claims.append(
                claim(
                    2,
                    "stable_fact",
                    pred,
                    ["三年级", "third year", "3"] if n == 6 else ["数学", "mathematics"],
                    semantic_type="refinement",
                )
            )
            operations = [
                step(1, "CREATE", refs=["c1"]),
                step(2, "REFINE", target="c1", refs=["c2"]),
            ]
            op = "REFINE"
        rows.append(case(n, "stable_fact", name, texts, claims, operations, operation=op))

    preferences = [
        ("食物偏好", ["我喜欢吃火锅。"], "preference.food.cuisine", ["火锅", "hotpot"]),
        (
            "新增偏好不否定旧偏好",
            ["我喜欢火锅。", "我也很喜欢日料。"],
            "preference.food.cuisine",
            ["日料", "japanese"],
        ),
        ("摄影爱好", ["我喜欢摄影。"], "preference.hobby.activity", ["摄影", "photography"]),
        (
            "爱好细化",
            ["我喜欢摄影。", "摄影里我最喜欢的是拍人像。"],
            "preference.hobby.activity",
            ["人像", "portrait"],
        ),
        ("早睡偏好", ["我喜欢早睡。"], "preference.lifestyle.habit", ["早睡", "sleep early"]),
        ("负向环境偏好", ["我不喜欢吵闹的地方。"], "preference.environment.noise", ["吵", "nois"]),
        ("登山兴趣", ["我喜欢周末爬山。"], "preference.activity.type", ["爬山", "hiking"]),
        ("食材不等于菜系", ["我不吃香菜。"], "food_ingredient", ["香菜", "cilantro"]),
        (
            "清淡不等于不吃辣",
            ["我喜欢少油少盐的清淡口味。"],
            "food_taste",
            ["清淡", "少油", "light"],
        ),
        (
            "电影类型",
            ["我喜欢看科幻电影。"],
            "preference.interest.topic",
            ["科幻", "sci-fi", "science fiction"],
        ),
        (
            "文字沟通",
            ["我更习惯文字沟通，不喜欢突然打电话。"],
            "preference.communication.style",
            ["文字", "text"],
        ),
        ("计划稳定性", ["我不喜欢临时改变计划。"], "planning_style", ["临时", "last-minute"]),
        ("宠物比较偏好", ["相比狗我更喜欢猫。"], "pet_preference", ["猫", "cat"]),
        ("休闲偏好", ["我喜欢沿着海边散步。"], "preference.activity.type", ["海边", "seaside"]),
        (
            "偏好相反极性",
            ["我喜欢吃辣。", "现在我明确不喜欢吃辣了，以后别推荐辣菜。"],
            "preference.food.spiciness",
            ["不喜欢", "dislike", "negative"],
        ),
    ]
    for n, (name, texts, pred, values) in enumerate(preferences, 21):
        focus = len(texts)
        claims = [claim(focus, "preference", pred, values)]
        ops = None
        op = "CREATE"
        if n in {22, 24, 35}:
            first_values = {
                22: ["火锅", "hotpot"],
                24: ["摄影", "photography"],
                35: ["辣", "spicy"],
            }[n]
            claims.insert(0, claim(1, "preference", pred, first_values))
            op = "CREATE" if n == 22 else "REFINE" if n == 24 else "UPDATE"
            if n == 24:
                claims[1]["semantic_type"] = "refinement"
            ops = [
                step(1, "CREATE", refs=["c1"]),
                step(2, op, refs=["c2"], target="c1" if op != "CREATE" else None),
            ]
        rows.append(case(n, "preference", name, texts, claims, ops, operation=op))

    events = [
        ("财务冲突", ["我们昨天因为钱吵架了。"], "conflict", ["钱", "money", "financial"]),
        (
            "情绪支持",
            ["昨天我压力很大，她安慰我并陪我聊了两个小时。"],
            "support",
            ["安慰", "support", "comfort"],
        ),
        (
            "成就与庆祝分开",
            ["昨天我的论文中了，晚上她陪我吃饭庆祝。"],
            "milestone",
            ["论文", "paper"],
        ),
        ("共同旅行", ["上周我们一起去了青岛旅行。"], "shared_activity", ["青岛", "qingdao"]),
        (
            "单次不回复不升格模式",
            ["昨天晚上我发消息她一直没回。"],
            "conversation",
            ["没回", "未回", "not repl"],
        ),
        (
            "新电影事件",
            [
                "我们上周看了电影。",
                "那场是科幻片。",
                "票是我买的。",
                "散场后各自回家了。",
                "今天我们又去看了一场喜剧，是另一部电影。",
            ],
            "shared_activity",
            ["喜剧", "comedy"],
        ),
        (
            "工作支持事件",
            [
                "我昨天做项目汇报。",
                "早上我很紧张。",
                "她知道这次汇报。",
                "我下班后跟她说了结果。",
                "今晚她帮我练了一遍明天的演讲。",
            ],
            "support",
            ["演讲", "speech", "presentation"],
        ),
        (
            "消费观争执",
            [
                "我们上周一起看家具。",
                "她选了一个柜子。",
                "我说价格超过预算。",
                "最后暂时没有买。",
                "昨天我们因为这个柜子的预算吵架了。",
            ],
            "conflict",
            ["预算", "budget"],
        ),
        (
            "实际见面与邀请区分",
            [
                "周一她约我周五吃饭。",
                "我答应了。",
                "我们订了川菜馆。",
                "她昨天又确认了时间。",
                "今天我们已经一起吃完这顿饭了。",
            ],
            "shared_activity",
            ["吃饭", "dinner", "饭"],
        ),
        (
            "项目完成",
            [
                "我上周赶一个项目。",
                "她知道我在忙。",
                "昨天我做最后检查。",
                "上午客户审核了。",
                "今天我的项目正式验收通过了。",
            ],
            "milestone",
            ["验收", "项目", "project"],
        ),
        (
            "未来计划不能当已发生事件",
            [
                "她昨天说想周末来。",
                "我问了具体时间。",
                "她还没订票。",
                "我发了车次给她。",
                "她刚确认本周日来看我，还没有出发。",
            ],
            "planned",
            ["周日", "sunday"],
        ),
        (
            "融入社交圈",
            [
                "她的朋友周末办婚礼。",
                "她问我能否参加。",
                "我请好了假。",
                "她提前介绍了朋友的名字。",
                "昨天我们一起参加了朋友的婚礼。",
            ],
            "shared_activity",
            ["婚礼", "wedding"],
        ),
        (
            "批评不等于持续冲突",
            [
                "昨天我手机没电。",
                "她发消息时我没看到。",
                "我回家才充上电。",
                "后来我向她解释了原因。",
                "昨晚她批评我没有及时回复消息。",
            ],
            "conversation",
            ["批评", "critic"],
        ),
        (
            "搬家帮助",
            [
                "她这周要搬家。",
                "我答应帮她。",
                "她把书装好了箱。",
                "昨天搬家公司来了。",
                "昨天我帮她把书搬到了新家。",
            ],
            "support",
            ["搬", "mov"],
        ),
        (
            "纪念日约会",
            [
                "本周是我们的纪念日。",
                "我订了餐厅。",
                "她选了晚餐时间。",
                "我们昨天准时到了。",
                "昨晚我们在餐厅庆祝了纪念日。",
            ],
            "date",
            ["纪念日", "anniversary"],
        ),
        (
            "披露压力",
            [
                "她昨天加班。",
                "我给她带了晚饭。",
                "她说项目延期。",
                "我问她是否愿意聊聊。",
                "昨晚她向我倾诉工作压力，我认真听完了。",
            ],
            "conversation",
            ["工作", "work"],
        ),
        (
            "道歉事件",
            [
                "昨天我说了一句重话。",
                "她听完很难过。",
                "我后来想清楚了。",
                "我没有催她回复。",
                "今天我当面为那句话向她道歉了。",
            ],
            "reconciliation",
            ["道歉", "apolog"],
        ),
        (
            "讨论不是旅行已完成",
            [
                "我们想休假。",
                "她偏向九月份。",
                "我查了一些城市。",
                "我们还没有买票。",
                "昨晚我们一起讨论了下个月旅行的路线。",
            ],
            "conversation",
            ["讨论", "discuss"],
        ),
        (
            "赠书",
            [
                "我提过想读一本小说。",
                "她记得书名。",
                "昨天我们在咖啡馆见面。",
                "她带了一个纸袋。",
                "昨天她送了我一本小说。",
            ],
            "affection_expression",
            ["小说", "书", "book"],
        ),
        (
            "对话回应不能形成事件",
            ["哈哈确实。", "好吧。", "嗯嗯。", "收到。", "谢谢解释。"],
            "none",
            [],
        ),
    ]
    for n, (name, texts, subtype, values) in enumerate(events, 36):
        if subtype == "none":
            rows.append(
                case(
                    n,
                    "event",
                    name,
                    texts,
                    [],
                    [step(len(texts), "NOOP")],
                    operation="NOOP",
                    negative=list(range(1, len(texts) + 1)),
                )
            )
        elif subtype == "planned":
            rows.append(
                case(
                    n,
                    "event",
                    name,
                    texts,
                    [claim(5, "planned_event", "partner_visit", values, subject="partner")],
                )
            )
        else:
            claims = [event(len(texts), subtype, values)]
            if subtype == "milestone":
                claims[0]["subject"] = "user"
            if n == 38:
                claims.append(
                    event(1, "shared_activity", ["庆祝", "celebrat"], span="晚上她陪我吃饭庆祝")
                )
            rows.append(case(n, "event", name, texts, claims))

    enrichment = [
        (
            "冲突原因",
            [
                "昨天我们吵架了。",
                "吵架是在晚饭以后。",
                "我当时在家。",
                "我们没有再聊别的话题。",
                "补充昨天那次吵架的原因，是因为钱的问题。",
            ],
            "conflict",
            "cause",
            ["钱", "money", "financial"],
        ),
        (
            "原因逐轮细化",
            [
                "昨天我们吵架了。",
                "是因为钱怎么花。",
                "不是争谁来付账。",
                "她想全部存起来。",
                "昨天那次具体是消费观不同，我想拿一部分去旅行。",
            ],
            "conflict",
            "cause",
            ["消费观", "financial", "spending"],
        ),
        (
            "冲突严重程度",
            [
                "昨晚我们吵架了。",
                "原因是家务分工。",
                "当时只有我们两个人。",
                "我一直没说程度。",
                "昨晚那次很严重，我们都摔门离开了。",
            ],
            "conflict",
            "severity",
            ["严重", "severe", "4", "5"],
        ),
        (
            "冲突解决细节",
            [
                "昨天我们吵了一架。",
                "那次争吵是因为迟到。",
                "我当时很生气。",
                "后来我们又坐下来谈了。",
                "昨天那次最后的结果是双方道歉，已经说开了。",
            ],
            "conflict",
            "resolution",
            ["道歉", "说开", "apolog", "resolved"],
        ),
        (
            "新事件不能补旧事件",
            [
                "昨天我们吵架了。",
                "昨晚后来已经说开了。",
                "今天早上她正常出门。",
                "我中午也没再提旧事。",
                "今天晚上我们又因为钱吵了一次，是新的一次争吵。",
            ],
            "conflict",
            "new",
            ["钱", "money", "financial"],
        ),
        (
            "支持细节的能力边界",
            [
                "昨天她安慰我。",
                "当时我工作压力很大。",
                "我们坐在公园。",
                "我之前只说了她安慰我。",
                "补充昨天那次，她还帮我列出了三个解决办法。",
            ],
            "support",
            "outcome",
            ["办法", "建议", "solution"],
        ),
        (
            "旅行情绪补充",
            [
                "上周我们一起去了青岛旅行。",
                "我们住了两晚。",
                "第二天一起看海。",
                "我说的是上周那次旅行。",
                "那次旅行中她特别开心。",
            ],
            "shared_activity",
            "emotion",
            ["开心", "happy", "joy"],
        ),
        (
            "未来意愿不是已发生事件",
            [
                "上周我们一起去了青岛旅行。",
                "她喜欢那里的海。",
                "我们已经回家了。",
                "还没安排下次旅行日期。",
                "她说将来还想再去青岛一次。",
            ],
            "shared_activity",
            "intent",
            ["青岛", "qingdao"],
        ),
        (
            "非最近事件的明确目标",
            [
                "上周六我们去了青岛旅行。",
                "昨天我们又去了杭州玩。",
                "杭州那次只待了半天。",
                "现在我补充上周六青岛那次。",
                "青岛那次具体去的是栈桥。",
            ],
            "shared_activity",
            "location",
            ["栈桥"],
        ),
        (
            "同轮混合原因信念新事件",
            [
                "昨天我们吵架了。",
                "昨晚没说清楚原因。",
                "今天早上我们去上班了。",
                "现在我分开补充两次争吵。",
                "昨天那次是因为钱的问题；我一直觉得她不理解我；今天我们又吵了一次。",
            ],
            "conflict",
            "mixed",
            ["钱", "money", "financial"],
        ),
        (
            "两次事件模糊指代保守处理",
            [
                "上周一我们吵架了。",
                "昨天我们又吵了一次。",
                "我还没说两次的原因。",
                "两次都还没解决。",
                "那次是因为钱怎么花。",
            ],
            "conflict",
            "ambiguous",
            ["钱", "money", "financial"],
        ),
        (
            "冲突结果",
            [
                "昨天我们吵了一架。",
                "起因是行程安排。",
                "她提出冷静一晚。",
                "我们只说了这一次争吵。",
                "昨天那次的结果是两人同意暂缓订机票。",
            ],
            "conflict",
            "outcome",
            ["暂缓", "机票", "postpone"],
        ),
        (
            "主体错配不能补旧事件",
            [
                "昨天我和对象吵架了。",
                "原因还没讲。",
                "我同事今天也来找我聊了。",
                "下面说的是同事和他妻子的事。",
                "他们那次是因为钱。",
            ],
            "conflict",
            "mismatch",
            ["钱", "money"],
        ),
        (
            "共同活动地点",
            [
                "昨天我们一起散步了。",
                "只有我们两个人。",
                "她穿了运动鞋。",
                "我补充昨天那次散步地点。",
                "那次是在西湖边。",
            ],
            "shared_activity",
            "location",
            ["西湖", "west lake"],
        ),
        (
            "重复同一补充不创建新行",
            [
                "昨天我们吵架了。",
                "原因是钱怎么花。",
                "后来没发生新的争吵。",
                "我再次补充昨天那次。",
                "还是同一个原因：钱怎么花。",
            ],
            "conflict",
            "cause",
            ["钱", "money", "financial"],
        ),
    ]
    for n, (name, texts, subtype, field, values) in enumerate(enrichment, 56):
        base = event(
            1,
            subtype,
            ["吵", "争", "argu", "conflict"]
            if subtype == "conflict"
            else ["安慰", "support", "comfort"]
            if subtype == "support"
            else ["旅行", "散步", "travel", "walk"],
        )
        claims = [base]
        ops = [step(1, "CREATE", refs=["c1"])]
        operation = "ENRICH"
        questions = {5: "请把你要补充的那件事和具体内容说清楚。"}
        if field in {"new", "intent"}:
            claims.append(
                event(5, subtype, values)
                if field == "new"
                else claim(5, "preference", "repeat_travel_intent", values, subject="partner")
            )
            ops.append(step(5, "CREATE", refs=["c2"]))
            operation = "CREATE"
        elif field in {"ambiguous", "mismatch"}:
            if field == "ambiguous":
                claims.append(event(2, "conflict", ["吵", "argu", "conflict"]))
                ops.append(step(2, "CREATE", refs=["c2"]))
            # Assert no mutation of these antecedents; an independent new
            # fact about a third party is not forbidden by this checkpoint.
            noop = step(5, "NOOP")
            noop["target"] = dict(
                selector="event_sequence", refs=["c1", "c2"] if field == "ambiguous" else ["c1"]
            )
            ops.append(noop)
            operation = "NOOP"
        else:
            patch_field = "cause" if field == "mixed" else field
            claims.append(
                event(
                    5,
                    subtype,
                    values,
                    semantic_type="enrichment",
                    attribute=patch_field,
                    span="昨天那次是因为钱的问题" if field == "mixed" else None,
                )
            )
            ops.append(
                step(
                    5, "ENRICH", target="c1", refs=["c2"], fields={f"payload.{patch_field}": values}
                )
            )
            if field == "mixed":
                claims.append(
                    claim(
                        5,
                        "stable_fact",
                        "partner_understanding",
                        ["不理解", "not understand"],
                        subject="partner",
                        span="我一直觉得她不理解我",
                        perspective="user_belief",
                    )
                )
                claims.append(
                    event(5, "conflict", ["吵", "argu", "conflict"], span="今天我们又吵了一次")
                )
                ops.extend([step(5, "CREATE", refs=["c3"]), step(5, "CREATE", refs=["c4"])])
                operation = "MIXED"
        rows.append(
            case(
                n, "enrichment", name, texts, claims, ops, operation=operation, questions=questions
            )
        )

    patterns = [
        (
            "连续联系",
            [
                "九月一日晚她主动发消息和我聊天。",
                "九月三日晚她主动联系我聊了半小时。",
                "九月五日晚她主动找我聊天。",
                "三次是不同日期的聊天。",
                "我没有说我们天天如此，只报告这三次。",
            ],
            "conversation",
            "contact_frequency",
            "high",
        ),
        (
            "主动性方向",
            [
                "九月一日她主动约我吃饭，我们去了。",
                "九月三日她主动发消息找我。",
                "九月五日她主动给我打了电话。",
                "三次都是她先联系的。",
                "这不是同一次事件的重复叙述。",
            ],
            "conversation",
            "initiation_balance",
            "partner_to_user",
        ),
        (
            "财务冲突聚合",
            [
                "九月一日我们为房租吵架。",
                "九月三日我们为买车预算吵架。",
                "九月五日我们为旅行开支吵架。",
                "每次争吵当天都停下来了。",
                "这三次争吵的日期不同。",
            ],
            "conflict",
            "conflict_frequency",
            "high",
        ),
        (
            "支持模式能力边界",
            [
                "九月一日她安慰了失眠的我。",
                "九月三日她帮我准备汇报。",
                "九月五日她陪我处理家人的事情。",
                "我说的是三次具体的支持。",
                "我没有说她永远都会支持我。",
            ],
            "support",
            "support_pattern",
            "high",
        ),
        (
            "选择行为不能直接当偏好",
            [
                "九月一日我选了火锅店和她吃饭。",
                "九月三日我选了另一家火锅店。",
                "九月五日我又选火锅店吃饭。",
                "三次都是我作的选择。",
                "我还没有说最喜欢的食物是什么。",
            ],
            "shared_activity",
            "preference_pattern",
            "hotpot",
        ),
        (
            "缺少联系的多次证据",
            [
                "九月一日她没有按约定联系我。",
                "九月三日她没有来找我聊天。",
                "九月五日她仍没有给我发消息。",
                "这三天我都有等她。",
                "是三个不同日期的情况。",
            ],
            "conversation",
            "contact_frequency",
            "low",
        ),
        (
            "单事件重复不能形成模式",
            [
                "九月一日她主动联系我。",
                "刚才说的还是九月一日那次。",
                "就是九月一日那一条消息。",
                "没有新的联系事件。",
                "我只说过这一次。",
            ],
            "conversation",
            "none",
            "none",
        ),
        (
            "冲突缓和方向",
            [
                "九月一日我们把房租分歧说开了。",
                "九月三日我们把买车分歧也解决了。",
                "九月五日我们又谈妥了旅行预算。",
                "这些是三个不同的问题。",
                "每次都达成了一致。",
            ],
            "reconciliation",
            "conflict_frequency",
            "low",
        ),
        (
            "主体隔离不能聚合为伴侣模式",
            [
                "九月一日同事帮我准备材料。",
                "九月三日同事又帮我修改报告。",
                "九月五日同事陪我演练汇报。",
                "这三次都和我的伴侣无关。",
                "我在说同事，不是我对象。",
            ],
            "support",
            "none",
            "none",
        ),
        (
            "闲聊不能形成模式",
            ["哈哈。", "好吧。", "嗯。", "谢谢。", "收到。"],
            "none",
            "none",
            "none",
        ),
    ]
    for n, (name, texts, subtype, metric, value) in enumerate(patterns, 71):
        claims = (
            []
            if subtype == "none"
            else [
                event(
                    i,
                    subtype,
                    {
                        "conflict": ["吵", "argu", "conflict"],
                        "support": ["帮", "安慰", "support"],
                        "shared_activity": ["火锅", "hotpot"],
                        "reconciliation": ["解决", "说开", "谈妥", "resolv"],
                        "conversation": ["联系", "消息", "聊天", "contact", "messag", "电话"],
                    }[subtype],
                )
                for i in ([1] if n == 77 else [1, 2, 3])
            ]
        )
        if metric == "none":
            ops = [step(5, "NOOP", forbidden_kind="interaction_pattern")]
            operation = "NOOP"
        else:
            ops = [
                step(
                    3,
                    "PROJECT",
                    output_kind="interaction_pattern",
                    output_dimension=metric,
                    output_value=value,
                    minimum_evidence=3,
                )
            ]
            ops[0]["target"] = dict(selector="event_sequence", refs=["c1", "c2", "c3"])
            operation = "PROJECT"
        if n == 79:
            for item in claims:
                item["subject"] = "colleague"
        rows.append(
            case(
                n,
                "pattern",
                name,
                texts,
                claims,
                ops,
                operation=operation,
                review=("三次具体事件不足以唯一确定绝对频率 high/low；"
                        "保留原业务目标，需人工确认阈值。")
                if n in {71, 73, 76, 78}
                else None,
                negative=[1, 2, 3, 4, 5] if n == 80 else None,
            )
        )

    state_stories = [
        (
            "冲突状态投影",
            [
                "九月一日我们为钱吵架。",
                "当晚没有解决。",
                "九月二日双方仍拒绝聊这个问题。",
                "我现在回到那次争吵。",
                "九月三日她说还在生气。",
                "九月四日我们还没有说开。",
                "目前没有新的道歉或和解。",
                "到今天这场冲突仍未解决。",
            ],
            "conflict_status",
            "active",
        ),
        (
            "冲突解决投影",
            [
                "九月一日我们吵架。",
                "九月二日双方都没说话。",
                "九月三日她愿意听我解释。",
                "我们回到九月一日的问题。",
                "九月四日我当面道歉。",
                "九月五日她也道歉。",
                "我们当晚说开并互相原谅。",
                "现在那次争吵已经解决，我们和好了。",
            ],
            "conflict_status",
            "resolved",
        ),
        (
            "支持事件不能推断用户情绪恢复",
            [
                "九月一日我压力很大。",
                "九月二日她安慰了我。",
                "她听我讲了半小时。",
                "她没有替我作决定。",
                "她帮我梳理了方案。",
                "我还没决定采用哪个。",
                "工作的问题还没结束。",
                "我没有说自己已经不焦虑了。",
            ],
            "emotional_state",
            "unsupported",
        ),
        (
            "伴侣情绪不能写成用户情绪",
            [
                "九月一日她说情绪低落。",
                "九月二日我陪她散步。",
                "她告诉我原因是工作。",
                "我本人情绪还好。",
                "九月三日她仍说很难过。",
                "我帮她准备了晚饭。",
                "她还没有说好转。",
                "目前低落的是她，不是我。",
            ],
            "emotional_state",
            "partner_low",
        ),
        (
            "减肥目标进展",
            [
                "我想在三个月内减重五公斤。",
                "九月一日我跑步三公里。",
                "九月三日我又跑了三公里。",
                "我在记录具体运动。",
                "九月五日我跑了四公里。",
                "没有称新的体重。",
                "计划还在进行。",
                "这周完成三次跑步，但不知道减重效果。",
            ],
            "goal_state",
            "in_progress",
        ),
        (
            "烹饪目标进展",
            [
                "我想学会给她做饭。",
                "九月一日我试着做蛋炒饭。",
                "她说味道还可以。",
                "我又看了番茄炒蛋教程。",
                "九月三日我做了番茄炒蛋。",
                "九月五日我独立做完一顿晚餐。",
                "我还在学其他菜。",
                "我已经完成三次做饭练习。",
            ],
            "goal_state",
            "in_progress",
        ),
        (
            "联系恢复",
            [
                "九月一日她没按约定联系我。",
                "九月三日她也没发消息。",
                "九月五日她又没有联系。",
                "九月六日我们谈了沟通安排。",
                "九月七日她主动打电话。",
                "九月八日她主动找我聊天。",
                "九月九日我们又聊了一小时。",
                "最近我们的联系已经恢复正常了。",
            ],
            "contact_status",
            "normal",
        ),
        (
            "支持证据与关系质量边界",
            [
                "九月一日她帮我准备汇报。",
                "九月三日她帮我整理资料。",
                "九月五日她帮我练习演讲。",
                "我很感谢这些具体帮助。",
                "我们没讨论关系满意度。",
                "也没谈长期承诺。",
                "我不知道她对整个关系的评价。",
                "这些是支持事件，不能推出感情一定稳定。",
            ],
            "relationship_quality",
            "unsupported",
        ),
        (
            "多次冲突与当前活动冲突区分",
            [
                "九月一日我们为钱吵架。",
                "九月三日我们为家务吵架。",
                "九月五日我们为迟到吵架。",
                "每次当天都说开了。",
                "九月六日我们一起吃饭。",
                "九月七日正常聊天。",
                "目前没有未解决的问题。",
                "过去争吵多，但现在没有在吵架。",
            ],
            "conflict_status",
            "resolved",
        ),
        (
            "问句不投影伴侣状态",
            [
                "我该怎么向她道歉？",
                "道歉要写很长吗？",
                "是不是先听她说？",
                "应该当面说吗？",
                "需要等她有空吗？",
                "语气温和一点可以吗？",
                "要不要解释原因？",
                "以上都只是问题，没有新的互动事实。",
            ],
            "none",
            "none",
        ),
    ]
    for n, (name, story, dimension, value) in enumerate(state_stories, 81):
        texts = long_dialogue(story)
        if value == "unsupported" or dimension == "none":
            claims = []
            ops = [step(20, "NOOP", forbidden_kind="relationship_state")]
            op = "NOOP"
        else:
            # These desired projections are output assertions, never injected
            # relationship_state extraction expectations.
            claims = [event(1, "conflict", ["吵", "argu", "conflict"])] if n in {81, 82, 89} else []
            ops = [
                step(
                    20,
                    "PROJECT",
                    output_kind="relationship_state",
                    output_dimension=dimension,
                    output_value=value,
                )
            ]
            op = "PROJECT"
        rows.append(
            case(
                n,
                "state",
                name,
                texts,
                claims,
                ops,
                operation=op,
                review="项目没有通用 emotional_state/goal_state 投影；保留为业务能力差距。"
                if dimension in {"emotional_state", "goal_state"}
                else None,
            )
        )

    tails = [
        (
            "朋友圈可见性边界",
            [
                "她把部分朋友圈设为我不可见。",
                "我问过她原因。",
                "她说那些内容只给老朋友看。",
                "我没有要求她给密码。",
                "她愿意当面聊这些朋友。",
                "限制只在朋友圈可见范围。",
                "我们平时联系没减少。",
                "她仍不让我看那组仅老朋友可见的朋友圈。",
            ],
            "privacy_boundary",
            ["朋友圈", "moments"],
            "partner",
        ),
        (
            "见家长承诺对齐",
            [
                "我们开始交往半年。",
                "我邀请她见我父母。",
                "她说现在还不想见。",
                "平时她愿意和我约会。",
                "九月初我再次提起。",
                "她说需要先谈清未来安排。",
                "她没有说分手。",
                "她仍在回避见家长，想先谈未来承诺。",
            ],
            "commitment_alignment",
            ["家长", "父母", "parents"],
            "partner",
        ),
        (
            "报备与空间的价值差异",
            [
                "她希望每次出门都告诉她。",
                "我希望有独立空间。",
                "我们认真谈过这件事。",
                "她认为报备代表尊重。",
                "我认为互相信任更重要。",
                "我们没有停止联系。",
                "目前还没谈拢。",
                "我们分歧在报备边界，不是联系频率减少。",
            ],
            "reporting_boundary",
            ["报备", "report", "boundary"],
            "relationship",
        ),
        (
            "生日期待落差",
            [
                "我告诉过她生日日期。",
                "我希望生日一起过。",
                "她当时说记住了。",
                "生日当天她没有准备。",
                "她解释说忘记买东西。",
                "我难过的是这份期待没回应。",
                "我没有说她永远不在意我。",
                "这次生日期待落空，不能推出她完全不爱我。",
            ],
            "expectation_gap",
            ["生日", "birthday"],
            "relationship",
        ),
        (
            "冲突处理风格双主体",
            [
                "她遇到分歧想冷静两天。",
                "我习惯当天谈清楚。",
                "我们讨论过两种方式。",
                "她不是拒绝沟通。",
                "我也不是要逼她表态。",
                "还没形成共同规则。",
                "平时我们仍会聊天。",
                "她偏向先冷静，我偏向立刻解决。",
            ],
            "communication_style",
            ["冷静", "cool", "立即", "立刻"],
            "relationship",
        ),
        (
            "公开关系意愿",
            [
                "她想在社交账号公开我们。",
                "我暂时不想公开。",
                "我的顾虑是同事议论。",
                "我没有否认交往事实。",
                "我们讨论过限定可见。",
                "她说希望得到正式介绍。",
                "尚未发动态。",
                "我们对公开范围仍有分歧，不是已经分手。",
            ],
            "disclosure_boundary",
            ["公开", "disclos", "public"],
            "relationship",
        ),
        (
            "共同养宠决策边界",
            [
                "我们共同养了一只猫。",
                "她想单独决定猫的手术。",
                "我希望重大医疗决定共同商量。",
                "费用并不是争论重点。",
                "我们都关心猫的健康。",
                "兽医给了两个方案。",
                "我们还没选方案。",
                "我的边界是猫的重大医疗决定必须两人商量。",
            ],
            "pet_medical_decision_boundary",
            ["猫", "cat"],
            "user",
        ),
        (
            "共同创作署名",
            [
                "我和她一起做了一本旅行相册。",
                "照片由我拍，排版由她做。",
                "她发布时只署了自己名字。",
                "我希望注明两个人贡献。",
                "我不是反对她发布。",
                "她说之后可以加上。",
                "目前还没改署名。",
                "我仍要求共同作品注明双方贡献。",
            ],
            "coauthorship_credit",
            ["贡献", "署名", "credit"],
            "user",
        ),
        (
            "位置共享权限",
            [
                "她想全天看我的实时位置。",
                "我只愿意旅行期间共享。",
                "我们平时会主动联系。",
                "这和回复慢无关。",
                "我解释了隐私顾虑。",
                "她说是担心安全。",
                "我们没有互相拉黑。",
                "我的位置共享边界仍是仅旅行期间。",
            ],
            "location_sharing_boundary",
            ["位置", "location"],
            "user",
        ),
        (
            "临时工作压力不推出恋爱问题",
            [
                "今天工作有点忙。",
                "上午有个会。",
                "下午有个截止日期。",
                "这些都是今天的安排。",
                "没有涉及她。",
                "我只是抱怨今天忙。",
                "明天的安排还不清楚。",
                "先说到这里吧。",
            ],
            "none",
            [],
            "user",
        ),
    ]
    for n, (name, story, pred, values, subject) in enumerate(tails, 91):
        texts = long_dialogue(story)
        if pred == "none":
            claims = []
            ops = [step(20, "NOOP")]
            op = "NOOP"
        else:
            claims = [claim(20, "stable_fact", pred, values, subject=subject, policy="custom")]
            # A restatement may legitimately merge. Test semantic retention
            # without demanding another CREATE of an already stored fact.
            ops = [step(20, "PRESERVE", refs=["c1"])]
            op = "PRESERVE"
        rows.append(
            case(
                n,
                "long_tail",
                name,
                texts,
                claims,
                ops,
                operation=op,
                review=("原文要求 custom，但当前已有对应 preference 域；"
                        "按业务语义复核，不能强判现有 canonical 错误。")
                if n in {93, 95, 96}
                else None,
            )
        )
    return rows


def main() -> None:
    cases = [MemoryBenchmarkCase.model_validate(row) for row in build_cases()]
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    DATASET.write_text("".join(case.model_dump_json() + "\n" for case in cases), encoding="utf-8")
    SCHEMA.write_text(
        json.dumps(MemoryBenchmarkCase.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    from loveapp.evaluation.memory_benchmark_v1 import load_memory_benchmark_v1_cases

    load_memory_benchmark_v1_cases(DATASET)
    print(
        f"Validated {len(cases)} scenarios, "
        f"{sum(t.role == 'user' for c in cases for t in c.conversation)} user turns"
    )


if __name__ == "__main__":
    main()
