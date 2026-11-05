module thermostat (
    input logic i_clk, i_rst,
    input logic [5:0] i_temp_feedback,
    input logic i_fan_on, i_enable, i_fault, i_clr,
    output logic o_heater_full, o_heater_medium, o_heater_low,
    output logic o_aircon_full, o_aircon_medium, o_aircon_low, o_fan,
    output logic [2:0] o_state
);
    logic fault_latched;
    logic [2:0] next_state;
    wire blocked=i_fault || (fault_latched && !i_clr) || !i_enable;
    always_comb begin
        next_state=3;
        if (!blocked && !i_clr) begin
            if (i_temp_feedback[5]) next_state=2;
            else if (i_temp_feedback[4]) next_state=1;
            else if (i_temp_feedback[3]) next_state=0;
            else if (i_temp_feedback[0]) next_state=6;
            else if (i_temp_feedback[1]) next_state=5;
            else if (i_temp_feedback[2]) next_state=4;
        end
    end
    always_ff @(posedge i_clk or negedge i_rst) begin
        if (!i_rst) begin
            fault_latched<=0; o_state<=3;
            o_heater_full<=0; o_heater_medium<=0; o_heater_low<=0;
            o_aircon_full<=0; o_aircon_medium<=0; o_aircon_low<=0; o_fan<=0;
        end else begin
            fault_latched<=i_fault || (fault_latched && !i_clr);
            o_state<=next_state;
            o_heater_full<=next_state==2; o_heater_medium<=next_state==1; o_heater_low<=next_state==0;
            o_aircon_full<=next_state==6; o_aircon_medium<=next_state==5; o_aircon_low<=next_state==4;
            o_fan<=!blocked && (next_state!=3 || i_fan_on);
        end
    end
endmodule
