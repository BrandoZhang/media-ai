# volc-ark/seedream-5.0 — notes

> Parameters and limits: `media-ai capabilities --binding volc-ark/seedream-5.0`.
> This file records the API behavior that capability output cannot express.

## Grouped image generation

Use `--count` to ask for a coherent set. It maps to Ark's
`sequential_image_generation=auto` and `max_images`; each result is reported in
`artifacts[]`, with later images marked `role: "group"`. References and generated
images share the binding's total-image budget, so query capabilities before choosing a
large set.

```bash
media-ai image edit --binding volc-ark/seedream-5.0 \
  --reference person.png outfit.png --count 3 --resolution 2K \
  --prompt "three connected scenes: morning, noon, and night" --output scenes.jpg
```

Remote HTTPS references are forwarded to Ark unchanged and retain their input order,
so prompts can refer to “image 1” and “image 2”.

## Streaming response

`--option stream=true` requests Ark's server-sent-event response. The CLI waits for
the finished stream, orders images by Ark's `image_index`, downloads them, and still
prints one final JSON result — it does not interleave events with stdout.

Use `--option stream=false` only when the endpoint needs that field explicitly; when
omitted, the adapter leaves it off the wire. `--option response_format=b64_json` is
available when a signed URL cannot be fetched from the calling network.
