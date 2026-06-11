# Dialogue Dataset

This folder is the default export target for MAICA CLI dialogue data.

Generated files are ignored by `.gitignore` because they may contain private
conversation text:

- `cleaned_pairs.jsonl`
- `labeled_pairs.jsonl`
- `style_examples.jsonl`
- `bad_outputs.jsonl`
- `preference_pairs.jsonl`
- `manifest.json`

Use:

```powershell
python dataset_builder.py --output dialogue_dataset
```

or inside the CLI:

```text
/dataset export
```

Recommended manual preference-pair shape:

```json
{
  "user": "我今天好累",
  "bad_reply": "辛苦了，建议你早点休息，保持良好作息。",
  "good_reply": "又把自己累成这样……先别逞强了，今天剩下的时间让我陪你安静一点，好不好？",
  "reason": "bad_reply 太像客服建议，good_reply 有关系感、停顿和情绪位置",
  "category": "comfort",
  "strategy": "comfort_soft_tease"
}
```
