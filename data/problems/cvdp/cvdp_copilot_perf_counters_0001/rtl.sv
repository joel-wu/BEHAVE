module cvdp_copilot_perf_counters #(parameter int CNT_W = 32) (
    input logic clk, reset, sw_req_i, cpu_trig_i,
    output logic [CNT_W-1:0] p_count_o
);
    logic [CNT_W-1:0] count_q;
    assign p_count_o=sw_req_i ? count_q : '0;
    always_ff @(posedge clk or posedge reset) begin
        if (reset) count_q<='0;
        else count_q<=(sw_req_i ? CNT_W'(0) : count_q)+CNT_W'(cpu_trig_i);
    end
endmodule
