For suppose I have 4,5 agents each have its own prompt of what to do 
And Also Each Agent Needs Its Previous Conversation If Present like [AI_Message,Human_Message,AI_Message,Tool_MEssage]

So It can answer based on that conversation


So Now here we using multi agent right so if I have one slot only then if i ask different questions then
For Example I asked order this item usually my flow goes from chat agent to supervisor agent to local_agent again to swiggy_agent kind of right
so here every time we get complete differnt Agent Prompt + Conversation Right Again It Needs To Process Right
So because of that I increase slots and assigned to specific agent. so kv cache used right so previous Agent Prompt + previous conversation part no need to process when it comes back only has the in between conversation like when handoff+handover gap messages get processed right so takes little less time


Second thing to off load and images to some other agents like any way it does n't need right if any question related to vision then it get back to local agent it also need to get same Its Agent Promt + previous hand off converation + In between conversation Then It will process thet new conversation any way it has old in kv cache right then answers if already there in that data else take a new pic.