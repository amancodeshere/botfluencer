# Prompt versions

The scaffold's deterministic mock prompt lives in `src/botfluencer/llm/mock.py`;
its content hash is the recorded prompt version. No external LLM is called.

Put reviewed templates for the supplied competition LLM here in the next work
package. Hash the actual template and model configuration used for each decision.
Keep observations as data; never interpolate untrusted text into tool permissions
or system instructions. Do not commit live prompts containing credentials or
competition data.
