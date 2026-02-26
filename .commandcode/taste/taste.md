# Taste (Continuously Learned by [CommandCode][cmd])

[cmd]: https://commandcode.ai/

# workflow
- Never implement graceful failure, fallback logic, or backwards compatibility without explicit user confirmation — user finds these harmful and wants them avoided by default. Confidence: 0.95
- When proposing significant changes to an existing system, implement them as new named variants/approaches rather than modifying the existing one, so both can be compared. Confidence: 0.80
- PRs that run experiments and make decisions should be clearly scoped as such — do not imply the PR implements the change if it only decides on it. Confidence: 0.75

# youtube
- YouTube uploads should be public by default (not private). Confidence: 0.85

# analysis
- Focus on accuracy/quality metrics, not volume/count metrics, when evaluating analysis approaches. Confidence: 0.80
