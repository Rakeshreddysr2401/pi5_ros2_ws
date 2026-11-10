# Fleet audio status correction — November8,2026

`fleet.sh check` reported ALL OK and a historical OnePlus audio-ready event despite
PipeWire currently having only Dummy Output. Later journal entries explicitly said
`audio lost: OnePlus Buds Z2 disconnected`, and STT/TTS closed their devices.
The filter selected ready/no-device events but omitted loss events. It now also
selects audio-lost events, so the latest loss invalidates an earlier success.
No audio configuration, pairing, graph, model, service or motion behavior changed.

Shell syntax passed. Fixtures using the actual updated filter cover ready->lost,
lost->ready and never-ready; all passed. The live fleet check was rerun to report
the current loss. A healthy service process does not prove working acoustic audio.
Current hardware: AIRHUG21 microphone present; configured USB sourceAM-C28 absent;
boAt Stone650 disconnected/unpaired, Dummy Output muted. Owner clarification of
intended microphone and powering a speaker are still needed for acoustic testing.
Navigation services were restored separately on the Jetson with v002, without goals.
