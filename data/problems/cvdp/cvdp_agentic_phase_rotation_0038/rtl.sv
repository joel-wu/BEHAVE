module control_fsm #(parameter int NBW_WAIT=32)(
    input logic clk, rst_async_n,
    input logic i_enable, i_subsampling, i_valid, i_calc_valid, i_calc_fail,
    input logic [NBW_WAIT-1:0] i_wait,
    output logic o_subsampling, o_valid, o_start_calc
);
    localparam CONTROL=0, CAPTURE=1, START=2, CALCULATE=3, WAITING=4;
    logic [2:0] phase;
    logic [8:0] remaining;
    logic [NBW_WAIT-1:0] saved_wait, timeout_count;
    assign o_valid=(phase==CAPTURE) && i_valid && (remaining!=0);
    assign o_start_calc=(phase==START) && (remaining==16);
    always_ff @(posedge clk or negedge rst_async_n) begin
        if(!rst_async_n) begin
            phase<=CONTROL; remaining<=0; saved_wait<=0; timeout_count<=0; o_subsampling<=0;
        end else case(phase)
            CONTROL: if(i_enable) begin
                o_subsampling<=i_subsampling; saved_wait<=i_wait;
                remaining<=i_subsampling ? 9'd256 : 9'd8; phase<=CAPTURE;
            end
            CAPTURE: if(i_valid) begin
                if(remaining==1) begin remaining<=16; phase<=START; end
                else remaining<=remaining-1'b1;
            end
            START: if(remaining==1) begin remaining<=0; phase<=CALCULATE; end
                   else remaining<=remaining-1'b1;
            CALCULATE: if(i_calc_fail) phase<=CONTROL;
                else if(i_calc_valid) begin timeout_count<=saved_wait; phase<=WAITING; end
            WAITING: if(!i_enable || timeout_count==0) phase<=CONTROL;
                else timeout_count<=timeout_count-1'b1;
            default: phase<=CONTROL;
        endcase
    end
endmodule
