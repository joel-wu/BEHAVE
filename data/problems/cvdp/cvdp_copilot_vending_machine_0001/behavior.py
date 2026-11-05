class VendingMachine:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state=0
        self.item=0
        self.credit=0
        self.error_pending=0
        self.previous_button=0
        self.previous_cancel=0

    def process(self, item_button, item_selected, coin_input, cancel):
        dispense_item=int(self.state==3)
        return_change=int(self.state==4)
        item_price=5*self.item if self.state==2 or self.state==3 else 0
        change_amount=self.credit-5*self.item if self.state==4 else 0
        dispense_item_id=self.item if self.state==3 else 0
        error=int(self.state==5 and self.error_pending)
        return_money=int(self.state==5 and not self.error_pending and self.credit>0)
        pressed=item_button and not self.previous_button
        cancelled=cancel and not self.previous_cancel
        self.previous_button=item_button
        self.previous_cancel=cancel
        valid=coin_input==1 or coin_input==2 or coin_input==5 or coin_input==10
        if self.state==5:
            if self.error_pending:self.error_pending=0
            else:self.state=0;self.item=0;self.credit=0
        elif self.state==4:
            self.state=0;self.item=0;self.credit=0
        elif self.state==3:
            if self.credit>5*self.item:self.state=4
            else:self.state=0;self.item=0;self.credit=0
        elif cancelled:
            self.state=5;self.error_pending=1
        elif (self.state==0 or self.state==1) and coin_input:
            if valid:self.credit+=coin_input
            self.state=5;self.error_pending=1
        elif self.state==0:
            if pressed:self.state=1
        elif self.state==1:
            if 1<=item_selected<=4:self.item=item_selected;self.state=2
            elif item_selected:self.state=5;self.error_pending=1
        elif coin_input:
            if not valid:self.state=5;self.error_pending=1
            else:
                self.credit+=coin_input
                if self.credit>=5*self.item:self.state=3
        return {"dispense_item":dispense_item,"return_change":return_change,
            "item_price":item_price,"change_amount":change_amount,
            "dispense_item_id":dispense_item_id,"error":error,"return_money":return_money}
