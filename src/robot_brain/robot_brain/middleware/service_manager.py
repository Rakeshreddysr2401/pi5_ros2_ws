class ServiceManager:

    def __init__(self, node):
        self.node = node
        self.clients = {}

    def register(self, name, srv_type):
        self.clients[name] = self.node.create_client(srv_type, name)

    async def call(self, name, request):
        client = self.clients[name]

        while not client.wait_for_service(timeout_sec=1.0):
            self.node.get_logger().info(f"Waiting for {name}")

        future = client.call_async(request)
        response = await future
        return response