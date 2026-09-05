# Memory Relation V1 Integration Diagnostic

Deterministic candidates were evaluated by the production relation resolver and committed through an isolated InMemoryMemoryStore. This is diagnostic only; no production Store mutation or model call is permitted.

Cases: `14`  
Relation matches: `14`  
Store writes attempted: `14`  
Transition audits: `14`  

| Case | Expected | Relation | Targets | Action | Final statuses | Passed |
|---|---|---|---|---|---|---|
| REL-001 | same | same | 70f8cc04-ef53-4380-9440-5bb219f9628c | merge | 70f8cc04-ef53-4380-9440-5bb219f9628c:confirmed | True |
| REL-003 | same | same | 354b0884-a99b-4616-8705-862c9caa0158 | merge | 354b0884-a99b-4616-8705-862c9caa0158:confirmed | True |
| REL-011 | update | update | ba3cd234-9e63-4db2-bdd8-e61a55e5b697 | replace | ba3cd234-9e63-4db2-bdd8-e61a55e5b697:superseded, c4a1dc53-2c83-4fa2-b88b-da51173382bc:confirmed | True |
| REL-018 | update | update | 20784dfa-c9c8-48c2-a870-baf8d7f0395c | replace | 20784dfa-c9c8-48c2-a870-baf8d7f0395c:superseded, 895420bc-019d-496a-8f10-41628235bf8d:confirmed | True |
| REL-023 | contradiction | contradiction | e1ff834e-6ec2-4ebe-8811-9058c1f240a8 | add | e1ff834e-6ec2-4ebe-8811-9058c1f240a8:confirmed, f81301d4-a27b-40f4-b638-0f659f2ffc8d:proposed | True |
| REL-030 | contradiction | contradiction | 0767d4b6-6554-4703-b32e-71e73ea48fbf | add | 0767d4b6-6554-4703-b32e-71e73ea48fbf:confirmed, a13d7e79-3a98-431d-8b27-482b1320782f:proposed | True |
| REL-033 | complementary | complementary | 96c01f51-5475-4c09-8ef6-8adafe89eccc | add | 96c01f51-5475-4c09-8ef6-8adafe89eccc:confirmed, 6d7533f0-2ebc-4bd6-8086-7c2c7e7bf70a:confirmed | True |
| REL-039 | complementary | complementary | 0f2393d5-3070-4480-86c4-67975bee21c1 | add | 0f2393d5-3070-4480-86c4-67975bee21c1:confirmed, a97a0407-65df-45f0-a55c-0f8273711518:confirmed | True |
| REL-043 | uncertain | uncertain | - | add | 4200c329-671e-4ffb-a79c-06e82d33d893:confirmed | True |
| REL-044 | uncertain | uncertain | 256041b8-1ff4-4531-9173-6d2fd1038520 | add | 256041b8-1ff4-4531-9173-6d2fd1038520:confirmed, cf86fc55-8cd8-4192-88a7-06329f77cd27:confirmed | True |
| REL-053 | unrelated | unrelated | - | add | 7e4cef8b-cc78-4b6f-9390-9c69348de280:confirmed, 117b25ad-3e02-4263-9c85-8cad085b118d:confirmed | True |
| REL-054 | unrelated | unrelated | - | add | e2fa4373-1a59-4d40-8940-a003a68a8db0:confirmed, ec99c685-7a66-4d2b-92cd-b9e540997cf1:confirmed | True |
| REL-028 | contradiction | contradiction | 60f69283-f866-40e5-b32e-91e5b1bef949 | add | 60f69283-f866-40e5-b32e-91e5b1bef949:confirmed, 66b61eae-e6a6-46fa-a278-7c6757be9f82:proposed | True |
| REL-051 | uncertain | uncertain | baf054f3-7a95-4032-8980-92a122e63921 | add | baf054f3-7a95-4032-8980-92a122e63921:proposed, 7eadc09f-f3c2-4138-85d4-093ab4c73c0b:confirmed | True |
