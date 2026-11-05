module pid_state_reset_f32 (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [3:0] in_base,
    output logic out_valid,
    input logic out_ready,
    output logic out_done,
    output logic mem_req_valid,
    output logic mem_req_we,
    output logic [3:0] mem_req_addr,
    output logic [31:0] mem_req_wdata,
    input logic mem_req_ready,
    input logic mem_resp_valid,
    input logic [31:0] mem_resp_rdata
);
    logic busy;
    logic [3:0] base;
    logic [1:0] offset;
    assign in_ready = !rst && !busy && !out_valid;
    assign mem_req_valid = !rst && busy;
    assign mem_req_we = mem_req_valid;
    assign mem_req_addr = busy ? base + offset : 4'd0;
    assign mem_req_wdata = 32'd0;
    always @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            base <= 0;
            offset <= 0;
            out_valid <= 0;
            out_done <= 0;
        end else begin
            if (out_valid && out_ready) out_valid <= 0;
            if (in_valid && in_ready) begin
                if (in_base <= 13) begin
                    base <= in_base;
                    offset <= 0;
                    busy <= 1;
                end else begin
                    out_done <= 0;
                    out_valid <= 1;
                end
            end
            if (mem_req_valid && mem_req_ready) begin
                if (offset == 2) begin
                    busy <= 0;
                    out_done <= 1;
                    out_valid <= 1;
                end else offset <= offset + 1'b1;
            end
        end
    end
endmodule
