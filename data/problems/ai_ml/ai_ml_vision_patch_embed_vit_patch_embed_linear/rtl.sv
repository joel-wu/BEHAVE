module ai_ml_vision_patch_embed_vit_patch_embed_linear #(
    parameter integer PATCH_H = 2,
    parameter integer PATCH_W = 2,
    parameter integer IN_CHANS = 3,
    parameter integer EMBED_DIM = 4,
    parameter integer DATA_W = 16
) (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [PATCH_H*PATCH_W*IN_CHANS*DATA_W-1:0] in_patch,
    input  logic [EMBED_DIM*PATCH_H*PATCH_W*IN_CHANS*DATA_W-1:0] in_weight,
    input  logic [EMBED_DIM*32-1:0] in_bias,
    input  logic in_use_bias,
    output logic out_valid,
    input  logic out_ready,
    output logic [EMBED_DIM*32-1:0] out_data
);
    localparam integer TERMS = PATCH_H * PATCH_W * IN_CHANS;
    logic [EMBED_DIM*32-1:0] projected;
    logic signed [DATA_W-1:0] pixel, weight;
    logic signed [2*DATA_W-1:0] product;
    logic signed [31:0] bias;
    logic signed [63:0] accumulator;

    always_comb begin
        projected = '0;
        pixel = '0;
        weight = '0;
        product = '0;
        bias = '0;
        accumulator = '0;
        for (int row = 0; row < EMBED_DIM; row++) begin
            bias = $signed(in_bias[row*32 +: 32]);
            accumulator = in_use_bias ? {{32{bias[31]}}, bias} : 64'sd0;
            for (int column = 0; column < TERMS; column++) begin
                pixel = $signed(in_patch[column*DATA_W +: DATA_W]);
                weight = $signed(in_weight[(row*TERMS+column)*DATA_W +: DATA_W]);
                product = pixel * weight;
                accumulator = accumulator + {{(64-2*DATA_W){product[2*DATA_W-1]}}, product};
            end
            projected[row*32 +: 32] = accumulator[31:0];
        end
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
            out_data <= '0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid)
                out_data <= projected;
        end
    end
endmodule
