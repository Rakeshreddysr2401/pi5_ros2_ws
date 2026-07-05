Goal is to Have Production Grade Working Robo That Can Lisen,Think,Talk,Move and Do Tasks in a Household Environment.


1.Speech related minimum requirements:
->invoke when we trigger with words like "Hey jarvis" later will change to custom name "Hey Chotu".And Start Lisening to the voice.
->even when it speaks after that for some time it should be able to listen and understand the commands for some seconds instead of always calling hey jarvis
->need better facilty like it can capable of playing music,telling jokes,answering questions kind of have proper way to handle all these
->when it playing music and we say "Hey jarvis stop playing music and come near to me or follow me" it should stop the music and listen to the command and then after that it should follow with that command  kind of things 


2. Movement related minimum requirements:
->for now it can just move with our commands thats fine
->in phase2 have slot for nav ,slam,nvblox kind of things when i get depth camera and other sensors in future

3.Vison related minimum requirements:
->need proper sync of all agents like they need to work together like a team and also capable of doing things/answering based on vision when nessary based on question.
->it needs to have proper capabilities like understanding the situations and also sending the image to teligrams or using for other purposes

4.teligram related minimum requirements:
->agent need to work properly when we send commands from telegram and also it should be able to send images and other things to telegram when we ask for it.
->not sure what all we can do with telegram but we can explore and add more features in future
->but make sure how we manage thing currently with voice and telegram i don't have much idea you take care of it.

5.archetecture related minimum requirements:
->need to have proper architecture my goal is to make this robo with more and more capabilities in future so need to have proper architecture and also need to have proper documentation for all the things we are doing so that in future we can easily add more features and also we can easily understand the code and also we can easily debug the issues.
->write production ready code with  proper latest langgraph features like having (swarn,subgraphs,deepagents,langchain all) and having structured ros all and proper brigge between these two all
->will add self learning capabilities in future so need to have proper architecture for that also and also need to have proper documentation for that also
->having prompts in single file.

6.Self learing:
not sure but what am thinking is some how using memo or some other way i can train or optimize my model montly 
or it can also can also remember related things right when answered.

7.I will also add nav,slam to my jetson in isaac_ros-dev
so,keep ready with agents and its prompts all.for now if its related questions come it says will coming soon


Simply what we need is a proper structured working production ready code with proper architecture and proper documentation for all the things we are doing so that in future we can easily add more features and also we can easily understand the code that fits into our jetson and pi5.
if required we can also add some cloud services which works with internet too like mem0,redis,qdrant vector db implemet tha code and update in .md how to configure.

IMPORTANT: These are some things that have in my mind but you have your freedom i need production ready working product that can saleble.


