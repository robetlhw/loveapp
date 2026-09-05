from loveapp.adapters.memory.openai_compatible import (
    _MEMORY_PROMPT_VERSION,
    _SYSTEM_PROMPT,
)


def test_extraction_prompt_uses_structured_short_reply_contract() -> None:
    assert _MEMORY_PROMPT_VERSION == "memory-v2.6"
    assert "previous_assistant_question、expected_slot、topic" in _SYSTEM_PROMPT
    assert "不要把\n  问答文本拼成新的 user_message" in _SYSTEM_PROMPT
    assert "requires_inference=true" in _SYSTEM_PROMPT
    assert "不知道/不确定/不想说" in _SYSTEM_PROMPT
    assert "不得用它填充原 expected_slot" in _SYSTEM_PROMPT
    assert "actor/cause/是否答案应补全为 interaction_event" in _SYSTEM_PROMPT
    assert "不得为发起者、\n  原因等开放属性伪造 relationship_state" in _SYSTEM_PROMPT


def test_extraction_prompt_separates_subject_from_perspective() -> None:
    assert "subject 回答“这个 proposition 描述哪个实体或哪段关系”" in _SYSTEM_PROMPT
    assert "payload.actor 只记录事件或行为的执行者" in _SYSTEM_PROMPT
    assert "三者相互独立" in _SYSTEM_PROMPT
    assert "subject=partner、perspective=user_belief" in _SYSTEM_PROMPT
    assert "subject=relationship、perspective=user_belief" in _SYSTEM_PROMPT
    assert "subject=user、perspective=user_reported" in _SYSTEM_PROMPT
    assert "谁执行了这个动作" in _SYSTEM_PROMPT
    assert "执行动作的 user 或 partner 是 subject" in _SYSTEM_PROMPT
    assert "没有以单个 actor 为语义焦点时，subject=relationship" in _SYSTEM_PROMPT
    assert "subject 只使用 user、\n   partner、relationship" in _SYSTEM_PROMPT
    assert "Advice 对关系产生的\n   outcome 也使用 relationship" in _SYSTEM_PROMPT
    assert "是我先提出暂停联系" in _SYSTEM_PROMPT
    assert "我们已经暂停联系两周" in _SYSTEM_PROMPT
    assert "她先提了分手" in _SYSTEM_PROMPT
    assert "我们已经正式分手" in _SYSTEM_PROMPT


def test_extraction_prompt_defines_independently_updateable_atomization() -> None:
    assert "独立确认、更新、否定、supersede 或删除" in _SYSTEM_PROMPT
    assert "回复速度、消息长度、主动发起、话题范围、互动渠道" in _SYSTEM_PROMPT
    assert "社交邀请、介绍朋友" in _SYSTEM_PROMPT
    assert "social invitation 与\n    friend introduction 两条" in _SYSTEM_PROMPT
    assert "advice_outcome 与当前冲突已解决的 relationship_state 分开" in _SYSTEM_PROMPT
    assert "不要过度拆分" in _SYSTEM_PROMPT
    assert "reply speed、message length、\n    topic initiation 三条 claims" in _SYSTEM_PROMPT
