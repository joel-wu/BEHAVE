class AxisJoiner:
    def __init__(self):
        self.reset()

    def reset(self):
        self.owner=0
        self.pending=0
        self.data=0
        self.last=0
        self.user=0

    def process(self, s_axis_tdata_1, s_axis_tvalid_1, s_axis_tlast_1, s_axis_tdata_2, s_axis_tvalid_2, s_axis_tlast_2, s_axis_tdata_3, s_axis_tvalid_3, s_axis_tlast_3, m_axis_tready):
        selected=self.owner
        if selected==0:
            if s_axis_tvalid_1:
                selected=1
            elif s_axis_tvalid_2:
                selected=2
            elif s_axis_tvalid_3:
                selected=3
        room=int((not self.pending or m_axis_tready) and not (self.pending and self.last))
        s_axis_tready_1=int(room and selected==1)
        s_axis_tready_2=int(room and selected==2)
        s_axis_tready_3=int(room and selected==3)
        m_axis_tdata=self.data if self.pending else 0
        m_axis_tvalid=self.pending
        m_axis_tlast=self.last if self.pending else 0
        m_axis_tuser=self.user if self.pending else 0
        busy=int(self.owner!=0)
        push=0
        data=0
        last=0
        if s_axis_tready_1 and s_axis_tvalid_1:
            push=1
            data=s_axis_tdata_1
            last=s_axis_tlast_1
        elif s_axis_tready_2 and s_axis_tvalid_2:
            push=1
            data=s_axis_tdata_2
            last=s_axis_tlast_2
        elif s_axis_tready_3 and s_axis_tvalid_3:
            push=1
            data=s_axis_tdata_3
            last=s_axis_tlast_3
        if self.pending and m_axis_tready:
            if self.last:
                self.owner=0
            self.pending=0
        if push:
            self.owner=selected
            self.pending=1
            self.data=data
            self.last=last
            self.user=selected
        return {"s_axis_tready_1":s_axis_tready_1,"s_axis_tready_2":s_axis_tready_2,"s_axis_tready_3":s_axis_tready_3,"m_axis_tdata":m_axis_tdata,"m_axis_tvalid":m_axis_tvalid,"m_axis_tlast":m_axis_tlast,"m_axis_tuser":m_axis_tuser,"busy":busy}
