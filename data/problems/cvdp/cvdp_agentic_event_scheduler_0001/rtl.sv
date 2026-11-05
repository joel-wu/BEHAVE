module event_scheduler (
    input logic clk, reset, add_event, cancel_event,
    input logic [3:0] event_id,
    input logic [15:0] timestamp,
    input logic [3:0] priority_in,
    output logic event_triggered,
    output logic [3:0] triggered_event_id,
    output logic error,
    output logic [15:0] current_time
);
    logic [15:0] event_timestamps [0:15], tmp_timestamps [0:15];
    logic [3:0] event_priorities [0:15], tmp_priorities [0:15];
    logic [15:0] event_valid,tmp_valid;
    logic [15:0] tmp_time;
    logic tmp_trigger,tmp_error;
    logic [3:0] tmp_id,best_priority;
    always_comb begin
        tmp_time=current_time+16'd10;
        tmp_valid=event_valid;
        for(int i=0;i<16;i++) begin
            tmp_timestamps[i]=event_timestamps[i]; tmp_priorities[i]=event_priorities[i];
        end
        tmp_error=0; tmp_trigger=0; tmp_id=0; best_priority=0;
        if(add_event) begin
            if(event_valid[event_id]) tmp_error=1;
            else begin
                tmp_valid[event_id]=1; tmp_timestamps[event_id]=timestamp;
                tmp_priorities[event_id]=priority_in;
            end
        end
        if(cancel_event) begin
            if(event_valid[event_id]) tmp_valid[event_id]=0;
            else tmp_error=1;
        end
        for(int i=0;i<16;i++) begin
            if(tmp_valid[i] && tmp_timestamps[i]<=tmp_time &&
               (!tmp_trigger || tmp_priorities[i]>best_priority)) begin
                tmp_trigger=1; tmp_id=4'(i); best_priority=tmp_priorities[i];
            end
        end
        if(tmp_trigger) tmp_valid[tmp_id]=0;
    end
    always_ff @(posedge clk) begin
        if(reset) begin
            current_time<=0; event_valid<=0; event_triggered<=0;
            triggered_event_id<=0; error<=0;
            for(int i=0;i<16;i++) begin event_timestamps[i]<=0; event_priorities[i]<=0; end
        end else begin
            current_time<=tmp_time; event_valid<=tmp_valid;
            event_triggered<=tmp_trigger; triggered_event_id<=tmp_id; error<=tmp_error;
            for(int i=0;i<16;i++) begin
                event_timestamps[i]<=tmp_timestamps[i]; event_priorities[i]<=tmp_priorities[i];
            end
        end
    end
endmodule
