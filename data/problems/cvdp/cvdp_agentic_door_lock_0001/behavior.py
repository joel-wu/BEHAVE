class DoorLock:
    def __init__(self, PASSWORD_LENGTH=4, MAX_TRIALS=3):
        self.PASSWORD_LENGTH = PASSWORD_LENGTH
        self.MAX_TRIALS = MAX_TRIALS
        self.reset()

    def reset(self):
        self.phase=0
        self.stored_password=1
        self.entered=0
        self.digits=0
        self.failures=0
        self.unlocked=0
        self.locked=0

    def process(self, key_input, key_valid, confirm, admin_override, admin_set_mode, new_password, new_password_valid):
        door_unlock=self.unlocked
        lockout=self.locked
        self.unlocked=0
        if self.phase==0:
            self.entered=0
            self.digits=0
            if admin_override:
                self.failures=0
                self.phase=6 if admin_set_mode else 3
            elif confirm:
                self.phase=4
            elif key_valid and key_input<10:
                self.entered=key_input
                self.digits=1
                self.phase=1
        elif self.phase==1:
            if confirm:
                self.phase=2 if self.digits==self.PASSWORD_LENGTH else 4
            elif key_valid and key_input<10 and self.digits<self.PASSWORD_LENGTH:
                self.entered=((self.entered<<4)|key_input)&((1<<(4*self.PASSWORD_LENGTH))-1)
                self.digits+=1
        elif self.phase==2:
            self.phase=3 if self.entered==self.stored_password else 4
        elif self.phase==3:
            self.unlocked=1
            self.locked=0
            self.failures=0
            self.phase=0
        elif self.phase==4:
            self.failures+=1
            if self.failures==self.MAX_TRIALS:
                self.locked=1
                self.phase=5
            else:
                self.phase=0
        elif self.phase==5:
            if admin_override:
                self.failures=0
                self.phase=3
        elif self.phase==6:
            if new_password_valid:
                self.stored_password=new_password
                self.phase=0
        return {"door_unlock":door_unlock,"lockout":lockout}
