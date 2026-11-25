# 2-minute demo video script

**0:00 Hook (10 s).** "At my cousin's wedding, half the family spoke Tamil, half Punjabi. The speeches? Lost. Meet Milan."

**0:10 Join (15 s).** Create the room "Priya weds Arjun" and show the QR code. Phone 1 picks Tamil, phone 2 Bengali, the laptop opens **Stage view** (`/stage/<room>?langs=ta-IN,pa-IN,bn-IN,en-IN`) on the big screen.

**0:25 The magic (35 s).** Hold the mic and speak Hinglish: *"Aap sab ka swagat hai, aaj ki shaam bahut khaas hai!"*
Each screen shows its own language and the voice plays. Point out: Saaras understood code-mixed speech, Sarvam-Translate, Bulbul voice.

**1:00 Flagsmith (20 s).** In the Flagsmith dashboard, remove `bn-IN` from `enabled_languages`, and the dropdown updates. Mention the `tts_voice` A/B experiment and the `elder_mode` segment (large text for elders).

**1:20 NudgeBee self-heal (35 s).** Run `make spike` (200 guests) and show latency climbing in NudgeBee. Run the **Sangeet Rush** workflow: AI root cause → approve → pods scale to 3 + HTTP call flips `rush_mode`. Every screen shows the "Rush mode" banner and captions keep flowing.

**1:55 Close (5 s).** "Milan: every family speaks, every family understands."

## Stage checklist
- [ ] `.env` filled; `make dev` works with real Sarvam
- [ ] Flagsmith features created (README table)
- [ ] kind cluster up, NudgeBee agent connected, workflow saved
- [ ] Phones on the same Wi-Fi; open `http://<laptop-ip>:8000/r/<room>`. Use `localhost` or HTTPS for the mic on phones. If phones can't use the mic over plain HTTP, they can listen and type.
- [ ] Public GitHub repo + demo video uploaded before **5:00 PM**
