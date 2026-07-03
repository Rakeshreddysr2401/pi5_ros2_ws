Fix If Any Major Issues And Any Important features if we missed.
One thing from my side:
idea is like having seperate slot for local_agent which can be used to answer visual related things.
like if we ask any question or intent related to visual content, the local_agent can handle it so one thing important is to maintaint  max possible first input need to be same so it useses kv cache and gets faster response . 
It uses combines ai:response,user:query and images it loaded so when user asks next any question then it tries to answer from that image in next round and if we change the input all the time it not effectively uses cache what i tought so it has all context

And for other agents no need of image in it any way what i tought and when some conversation happen out of local agent when we pass it to local agent need to add that messages alonge with its previously cached messages so it uses cache and  process this recent conversation and decide to answer directly if have info