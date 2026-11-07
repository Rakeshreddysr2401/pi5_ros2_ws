from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
from langrobo_core.utils.speech_stream import SpeechStreamHandler
from langrobo_ros.agent_node import AgentNode


def test_background_search_speaks_its_announcement_once():
    sent = []
    stream = SpeechStreamHandler(sent.append)
    node = SimpleNamespace(get_logger=lambda: SimpleNamespace(info=lambda *a: None))
    AgentNode._speak_around_tools(node, stream, AIMessage("", tool_calls=[{
        "name": "approach_described_object", "args": {"description": "the door"}, "id": "1"}]))
    AgentNode._speak_around_tools(node, stream, ToolMessage(
        "Looking for the door now -- searching in the background. The result follows.",
        name="approach_described_object", tool_call_id="1"))
    run = "test"
    stream.on_chat_model_start(None, None, run_id=run)
    stream.on_llm_new_token("I am looking for the door now.", run_id=run)
    stream.on_llm_end(None, run_id=run)
    assert sent == ["Looking for the door."]
    assert stream.unspoken("I am looking for the door now.") == ""
