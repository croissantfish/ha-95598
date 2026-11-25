class ShutdownRequested(SystemExit):
    def __init__(self):
        super().__init__(0)
