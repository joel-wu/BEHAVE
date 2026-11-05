module ai_ml_ssm_mamba_mamba_conv1d_depthwise #(
    parameter int D_CONV = 4,
    parameter int DATA_W = 32
) (
    input  logic                  clk,
    input  logic                  rst,

    input  logic                  in_valid,
    output logic                  in_ready,
    input  logic [DATA_W-1:0]     in_x,

    input  logic                  cfg_valid,
    input  logic                  cfg_we,
    input  logic [1:0]            cfg_addr,
    input  logic signed [DATA_W-1:0] cfg_wdata,

    output logic                  out_valid,
    input  logic                  out_ready,
    output logic [DATA_W-1:0]     out_y
);

logic busy;
logic signed [DATA_W-1:0] conv_state [0:D_CONV-1];
logic signed [DATA_W-1:0] weight     [0:D_CONV-1];
logic [DATA_W-1:0] out_y_reg;

wire input_accepted   = in_valid & in_ready;
wire output_consumed  = out_valid & out_ready;
// Configuration belongs to the accepted sample, not to a separate clock action.
wire cfg_write_hits   = cfg_valid & cfg_we & (int'(cfg_addr) < D_CONV);

// Combinational effective weight + next-state used for this transaction.
logic signed [DATA_W-1:0] weight_eff [0:D_CONV-1];
logic signed [DATA_W-1:0] next_state [0:D_CONV-1];
logic signed [2*DATA_W-1:0] preact;

always_comb begin
    for (int i = 0; i < D_CONV; i = i + 1) begin
        weight_eff[i] = (cfg_write_hits && int'(cfg_addr) == i) ? cfg_wdata : weight[i];
    end

    for (int i = 0; i < D_CONV - 1; i = i + 1) begin
        next_state[i] = conv_state[i + 1];
    end
    next_state[D_CONV - 1] = $signed(in_x);

    preact = '0;
    for (int i = 0; i < D_CONV; i = i + 1) begin
        preact = preact + (next_state[i] * weight_eff[i]);
    end
end

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;
assign out_y     = out_y_reg;

always_ff @(posedge clk) begin
    if (rst) begin
        busy      <= 1'b0;
        out_y_reg <= '0;
        for (int i = 0; i < D_CONV; i = i + 1) begin
            conv_state[i] <= '0;
            // Boxcar default: weight = [1, 1, 1, 1].
            weight[i] <= 1;
        end
    end else begin
        if (input_accepted) begin
            for (int i = 0; i < D_CONV; i = i + 1) begin
                conv_state[i] <= next_state[i];
                if (cfg_write_hits && int'(cfg_addr) == i)
                    weight[i] <= cfg_wdata;
            end
            out_y_reg <= preact[DATA_W-1:0];
            busy      <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end
end

endmodule
