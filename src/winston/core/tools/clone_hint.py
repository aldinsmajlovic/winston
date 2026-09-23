"""clone_voice on the typed channels (Talk page text, Discord, winston talk): cloning records the
friend through the voice channel's mic, so here the tool only says where to ask. Without it the
brain thought for seconds and then said it cannot clone voices at all."""
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

HINT = ("Cloning needs the person at a microphone, so it only works by voice: tap the talk button on the Talk page "
        "or the dashboard and ask there, with them in the room. Nothing was recorded.")


def make_clone_hint_tool():
    async def clone_voice(params: FunctionCallParams):
        await params.result_callback({"error": HINT})

    schema = FunctionSchema(
        name="clone_voice",
        description="Copy someone's voice, a friend's or the user's own. Call it when the user asks to clone, copy or "
                    "take a voice; it explains how, since in typed chat it cannot record.",
        properties={"name": {"type": "string", "description": "Whose voice, by first name, if given."}},
        required=[])
    return schema, clone_voice
