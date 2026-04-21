class TopicManager:

    def __init__(self, node):
        self.node = node
        self.publishers = {}
        self.subscribers = {}

    def create_publisher(self, name, msg_type):
        self.publishers[name] = self.node.create_publisher(msg_type, name, 10)

    def publish(self, name, msg):
        if name in self.publishers:
            self.publishers[name].publish(msg)

    def create_subscription(self, name, msg_type, callback):
        self.subscribers[name] = self.node.create_subscription(
            msg_type,
            name,
            callback,
            10
        )