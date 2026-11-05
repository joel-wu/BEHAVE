module correlate1d_valid3 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_base_addr,
    input  logic [15:0]  in_k0,
    input  logic [15:0]  in_k1,
    input  logic [15:0]  in_k2,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_corr,

    output logic         signal_req_valid,
    output logic [7:0]   signal_req_addr,
    input  logic         signal_req_ready,
    input  logic         signal_resp_valid,
    input  logic [15:0]  signal_resp_rdata
);

    typedef enum logic [2:0] {
        S_IDLE  = 3'd0,
        S_REQ0  = 3'd1,
        S_WAIT0 = 3'd2,
        S_REQ1  = 3'd3,
        S_WAIT1 = 3'd4,
        S_REQ2  = 3'd5,
        S_WAIT2 = 3'd6,
        S_OUT   = 3'd7
    } state_t;

    state_t state;

    logic [7:0]  base_addr_r;
    logic [15:0] k0_r;
    logic [15:0] k1_r;
    logic [15:0] k2_r;
    logic [15:0] x0_r;
    logic [15:0] x1_r;
    logic [15:0] x2_r;
    logic [31:0] acc_r;
    logic [31:0] out_corr_r;

    logic signed [15:0] resp_s16;
    logic signed [15:0] k0_s16;
    logic signed [15:0] k1_s16;
    logic signed [15:0] k2_s16;
    logic signed [31:0] prod0_now;
    logic signed [31:0] prod1_now;
    logic signed [31:0] prod2_now;
    logic signed [31:0] acc_s32;
    logic signed [31:0] sum1_now;
    logic signed [31:0] sum2_now;

    assign resp_s16  = signal_resp_rdata;
    assign k0_s16    = k0_r;
    assign k1_s16    = k1_r;
    assign k2_s16    = k2_r;
    assign prod0_now = resp_s16 * k0_s16;
    assign prod1_now = resp_s16 * k1_s16;
    assign prod2_now = resp_s16 * k2_s16;
    assign acc_s32   = acc_r;
    assign sum1_now  = acc_s32 + prod1_now;
    assign sum2_now  = acc_s32 + prod2_now;

    assign in_ready  = (state == S_IDLE);
    assign out_valid = (state == S_OUT);
    assign out_corr  = out_corr_r;

    always_comb begin
        signal_req_valid = 1'b0;
        signal_req_addr  = 8'h00;
        unique case (state)
            S_REQ0: begin
                signal_req_valid = 1'b1;
                signal_req_addr  = base_addr_r;
            end
            S_REQ1: begin
                signal_req_valid = 1'b1;
                signal_req_addr  = base_addr_r + 8'd1;
            end
            S_REQ2: begin
                signal_req_valid = 1'b1;
                signal_req_addr  = base_addr_r + 8'd2;
            end
            default: begin
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state       <= S_IDLE;
            base_addr_r <= 8'h00;
            k0_r        <= 16'h0000;
            k1_r        <= 16'h0000;
            k2_r        <= 16'h0000;
            x0_r        <= 16'h0000;
            x1_r        <= 16'h0000;
            x2_r        <= 16'h0000;
            acc_r       <= 32'h00000000;
            out_corr_r  <= 32'h00000000;
        end else begin
            unique case (state)
                S_IDLE: begin
                    if (in_valid && in_ready) begin
                        base_addr_r <= in_base_addr;
                        k0_r        <= in_k0;
                        k1_r        <= in_k1;
                        k2_r        <= in_k2;
                        x0_r        <= 16'h0000;
                        x1_r        <= 16'h0000;
                        x2_r        <= 16'h0000;
                        acc_r       <= 32'h00000000;
                        state       <= S_REQ0;
                    end
                end

                S_REQ0: begin
                    if (signal_req_valid && signal_req_ready) begin
                        state <= S_WAIT0;
                    end
                end

                S_WAIT0: begin
                    if (signal_resp_valid) begin
                        x0_r  <= signal_resp_rdata;
                        acc_r <= prod0_now;
                        state <= S_REQ1;
                    end
                end

                S_REQ1: begin
                    if (signal_req_valid && signal_req_ready) begin
                        state <= S_WAIT1;
                    end
                end

                S_WAIT1: begin
                    if (signal_resp_valid) begin
                        x1_r  <= signal_resp_rdata;
                        acc_r <= sum1_now;
                        state <= S_REQ2;
                    end
                end

                S_REQ2: begin
                    if (signal_req_valid && signal_req_ready) begin
                        state <= S_WAIT2;
                    end
                end

                S_WAIT2: begin
                    if (signal_resp_valid) begin
                        x2_r       <= signal_resp_rdata;
                        out_corr_r <= sum2_now;
                        state      <= S_OUT;
                    end
                end

                S_OUT: begin
                    if (out_valid && out_ready) begin
                        state <= S_IDLE;
                    end
                end
            endcase
        end
    end

endmodule