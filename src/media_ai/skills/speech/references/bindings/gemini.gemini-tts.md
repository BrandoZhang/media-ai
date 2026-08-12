# gemini/gemini-tts — notes

> Parameters and limits: `{{cli}} capabilities --binding gemini/gemini-tts`.
> This file is only what that output cannot tell you.

## What it is good at

**Being directed.** It is a language model that decides *how* to say the line, not a
voice engine with an emotion dial — so it takes a director's brief in prose and acts on
it. Tone, accent, pace, breathing and stage business all go into the text, not into
flags.

```bash
{{cli}} speech generate --binding gemini/gemini-tts \
    --text "Say cheerfully: We did it — the launch is live!" \
    --voice <voice-name> --output launch.wav
```

Voices are *named* rather than opaque ids, so one can be picked from its name without a
lookup call. `{{cli}} capabilities` lists them.

## Prompting

Two mechanisms, meant to be used together.

### 1. Inline audio tags

Lowercase square brackets, written into the text, scoped to what follows:

```
[whispers] Hey there — [shouting] and I can say things in many different ways.
```

They control emotion (`[excited]`, `[bored]`, `[curious]`, `[panicked]`), delivery
(`[whispers]`, `[shouting]`, `[sarcastic]`, `[trembling]`), pace (`[very fast]`,
`[very slow]`), and non-verbal sounds (`[laughs]`, `[sighs]`, `[gasp]`, `[cough]`,
`[giggles]`). Compound and inventive tags work too — `[one painfully slow word at a
time]`, `[like a cartoon dog]`. There is no closed vocabulary; it is worth trying the
thing you actually mean.

**Write the tags in English even when the transcript is not.** That is the vendor's
recommendation, and it holds for Chinese and Japanese scripts here.

### 2. A director's brief

For anything longer than a line, a structured brief outperforms an adjective. The
vendor's shape, in descending order of how much it matters:

- **Director's Notes** — the one section to always include. Usually **Style, Pacing,
  Accent**, but anything that matters to the performance belongs here.
- **Audio Profile** — a name and an archetype for the character. Naming them and then
  referring to them by name grounds the performance.
- **Scene** — where they are and what the room feels like. This shapes delivery
  indirectly and is what makes it sound unstaged.
- **Transcript** — the words. Its topic and register must match the direction.

**Be specific where specificity is cheap.** "British English as heard in Croydon"
beats "British accent"; "infectious enthusiasm — the listener should feel part of a
huge event" beats "energetic".

**But do not over-specify.** Piling on strict rules constrains the model and makes the
read worse, not better. Leave gaps for it to fill, the way you would with an actor.

On `speech dialogue`, that brief is what `--instruction` is for — it is prepended to
the script, so it applies to the whole cast, and per-speaker notes go in it by name:

```bash
{{cli}} speech dialogue --binding gemini/gemini-tts \
    --speaker Host=<voice> --speaker Guest=<voice> \
    --instruction "Late-night radio. Make Host warm and unhurried, Guest nervous and
                   over-caffeinated. Accent: both West Coast US." \
    --turn Host "Thanks for coming on." \
    --turn Guest "[laughs nervously] Happy to be here." \
    --output show.wav
```

On `speech generate` there is no `--instruction` — it is a dialogue-only flag. Put the
brief at the top of `--text` instead.

### 3. Cast the voice to match the direction

The voice is not a neutral carrier: a breathy voice sells "tired", an upbeat one sells
"excited", and a mismatch fights the direction rather than being overridden by it. Each
voice in `capabilities` carries a one-word character; pick on that, then direct.

## Traps

- **A vague prompt can get your director's notes read aloud.** If the model cannot tell
  direction from transcript, it either narrates the brief or the request is rejected as
  prohibited content. The fix is structural, not stylistic: **open with an explicit
  instruction to synthesize speech, and label where the spoken text begins.** The vendor
  documents this for its newer TTS preview model; it costs nothing to apply here, and it
  is the most likely cause of a take that reads your stage notes back to you.
- **A silent drop is an exit-8 safety error.** The API can return 200 with no audio
  part; that surfaces as a `safety` failure rather than a zero-byte file. Do not retry
  it unchanged — restructure the prompt per the point above, or change the text.
- **A random 500 is expected, and this CLI will not retry it.** The model occasionally
  emits text tokens instead of audio and the server fails the request. `HttpClient`
  retries transient 5xx only on GET/DELETE — never on a generate POST — so an isolated
  provider error here means *run the command again*, not *the request is malformed*.
- **WAV only.** `--output-format` has nothing to select; name the output `.wav` or you
  get WAV bytes in a file called `.mp3`. There is no `--seed` and no `--timestamps`
  sidecar either — for subtitles you need a binding that reports
  `supports.timestamps`.
- **The dialogue cast is small.** Check `constraints.audio.max_dialogue_voices` before
  writing a third character in; exceeding it is refused before the call. Speaker names
  in the script must match the `--speaker` names exactly.
- **Quality drifts on long reads.** Split a chapter into chunks rather than sending it
  whole; consistency degrades over a few minutes of audio.
- **Direction is charged as characters.** A three-paragraph director's brief costs the
  same per character as dialogue, on every take you iterate.

## Further reading

Vendor guide. It describes the model, not this CLI — flag names, limits and the machine
contract above are what `capabilities` says, whatever that page shows.

- Speech generation, including the prompting guide (audio tags, brief structure) —
  <https://ai.google.dev/gemini-api/docs/speech-generation>
