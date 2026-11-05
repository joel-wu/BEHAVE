module ai_ml_arch_crossbar_round_robin_arbiter #(
    parameter int N = 4,
    parameter int DATA_W = 32,
    parameter int INDEX_W = (N <= 1) ? 1 : $clog2(N)
) (
    input  logic                     clk,
    input  logic                     rst,

    input  logic                     in_valid,
    output logic                     in_ready,
    input  logic [N-1:0]             in_req,
    input  logic [N*DATA_W-1:0]      in_data,

    output logic                     out_valid,
    input  logic                     out_ready,
    output logic [DATA_W-1:0]        out_data,
    output logic [INDEX_W-1:0]       out_index
);

    logic [INDEX_W-1:0] last_grant;
    logic [INDEX_W-1:0] chosen;
    logic               has_request;

    integer offset;
    integer candidate;

    always_comb begin
        chosen = '0;
        has_request = 1'b0;

        for (offset = 1; offset <= N; offset = offset + 1) begin
            candidate = last_grant + offset;
            if (candidate >= N) begin
                candidate = candidate - N;
            end
            if (!has_request && in_req[candidate]) begin
                chosen = candidate[INDEX_W-1:0];
                has_request = 1'b1;
            end
        end

        in_ready = out_ready;
        out_valid = in_valid && has_request;
        out_data = in_data[chosen*DATA_W +: DATA_W];
        out_index = chosen;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            last_grant <= INDEX_W'(N - 1);
        end else if (out_valid && out_ready) begin
            last_grant <= chosen;
        end
    end

endmodule
