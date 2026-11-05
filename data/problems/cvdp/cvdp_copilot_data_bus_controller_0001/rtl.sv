module data_bus_controller #(parameter int AFINITY=0) (
    input logic clk, rst_n,
    input logic m0_valid,
    input logic [31:0] m0_data,
    input logic m1_valid,
    input logic [31:0] m1_data,
    input logic s_ready,
    output logic m0_ready, m1_ready, s_valid,
    output logic [31:0] s_data
);
    logic [1:0] selected;
    always_comb begin
        selected=2;
        if(m0_valid && m1_valid) selected=2'(AFINITY);
        else if(m0_valid) selected=0;
        else if(m1_valid) selected=1;
    end
    assign m0_ready=rst_n && s_ready && selected==0;
    assign m1_ready=rst_n && s_ready && selected==1;
    always_ff @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin s_valid<=0;s_data<=0;end
        else if(s_ready) begin
            s_valid<=selected!=2;
            s_data<=selected==0 ? m0_data : selected==1 ? m1_data : 32'b0;
        end
    end
endmodule
