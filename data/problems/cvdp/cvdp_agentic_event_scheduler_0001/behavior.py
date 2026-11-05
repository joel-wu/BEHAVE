class EventScheduler:
    def __init__(self):
        self.reset()

    def reset(self):
        self.time=0
        self.times=[0]*16
        self.priorities=[0]*16
        self.active=[0]*16
        self.trigger=0
        self.identity=0
        self.error=0

    def process(self, add_event, cancel_event, event_id, timestamp, priority_in):
        current_time=self.time
        event_triggered=self.trigger
        triggered_event_id=self.identity
        error=self.error
        self.time=(self.time+10)&65535
        self.error=0
        was_active=self.active[event_id]
        if add_event:
            if was_active:
                self.error=1
            else:
                self.active[event_id]=1
                self.times[event_id]=timestamp
                self.priorities[event_id]=priority_in
        if cancel_event:
            if was_active:
                self.active[event_id]=0
            else:
                self.error=1
        self.trigger=0
        self.identity=0
        best=0
        for i in range(16):
            if self.active[i] and self.times[i]<=self.time:
                if not self.trigger or self.priorities[i]>best:
                    self.trigger=1
                    self.identity=i
                    best=self.priorities[i]
        if self.trigger:
            self.active[self.identity]=0
        return {"event_triggered":event_triggered,"triggered_event_id":triggered_event_id,"error":error,"current_time":current_time}
