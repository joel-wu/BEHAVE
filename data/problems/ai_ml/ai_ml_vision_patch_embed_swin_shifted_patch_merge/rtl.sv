module ai_ml_vision_patch_embed_swin_shifted_patch_merge #(
    parameter int DATA_W = 16,
    parameter int IN_H = 4,
    parameter int IN_W = 4,
    parameter int DIM = 2,
    parameter int OUT_DIM = 4
) (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [IN_H*IN_W*DIM*DATA_W-1:0] in_x,
    output logic out_valid,
    input  logic out_ready,
    output logic [((IN_H+1)/2)*((IN_W+1)/2)*OUT_DIM*DATA_W-1:0] out_y
);
    localparam int N = 4 * DIM;
    localparam int OUT_H = (IN_H + 1) / 2;
    localparam int OUT_W = (IN_W + 1) / 2;
    localparam int OUT_BITS = OUT_H * OUT_W * OUT_DIM * DATA_W;

    function automatic logic [63:0] isqrt33(input logic [63:0] value);
        logic [63:0] root, candidate;
        root = 0;
        for (int bit_index = 16; bit_index >= 0; bit_index--) begin
            candidate = root | (64'd1 << bit_index);
            if (candidate * candidate <= value)
                root = candidate;
        end
        isqrt33 = root;
    endfunction

    function automatic longint signed saturate16(input longint signed value);
        if (value < -32768) saturate16 = -32768;
        else if (value > 32767) saturate16 = 32767;
        else saturate16 = value;
    endfunction

    function automatic logic [OUT_BITS-1:0] merge_tensor(
        input logic [IN_H*IN_W*DIM*DATA_W-1:0] tensor
    );
        longint signed merged [0:N-1];
        longint signed affine [0:N-1];
        longint signed total, mean, delta, variance, inv_std, norm, gamma, beta;
        longint signed weight, acc, clipped;
        int corner, h, w, index;
        logic [OUT_BITS-1:0] result;
        result = '0;
        for (int oh = 0; oh < OUT_H; oh++) begin
            for (int ow = 0; ow < OUT_W; ow++) begin
                total = 0;
                for (int k = 0; k < N; k++) begin
                    corner = k / DIM;
                    h = 2 * oh + corner % 2;
                    w = 2 * ow + corner / 2;
                    index = (h * IN_W + w) * DIM + k % DIM;
                    merged[k] = 0;
                    if (h < IN_H && w < IN_W)
                        merged[k] = longint'($signed(tensor[index*DATA_W +: DATA_W]));
                    total += merged[k];
                end
                mean = total / longint'(N);
                variance = 0;
                for (int k = 0; k < N; k++) begin
                    delta = merged[k] - mean;
                    variance += delta * delta;
                end
                variance = variance / longint'(N);
                inv_std = $signed(isqrt33(64'd4294967296 / (variance + 1)));
                for (int k = 0; k < N; k++) begin
                    norm = ((merged[k] - mean) * inv_std) / 256;
                    gamma = 256 + 32 * (longint'(k) % 3 - 1);
                    beta = 16 * (longint'(k) % 5 - 2);
                    affine[k] = saturate16((norm * gamma) / 256 + beta);
                end
                for (int o = 0; o < OUT_DIM; o++) begin
                    acc = 0;
                    for (int k = 0; k < N; k++) begin
                        weight = 32 * (((longint'(o) + 1) * (longint'(k) + 3)
                                        + longint'(o) * longint'(k)) % 17 - 8);
                        acc += (affine[k] * weight) / 256;
                    end
                    clipped = saturate16(acc);
                    index = (oh * OUT_W + ow) * OUT_DIM + o;
                    result[index*DATA_W +: DATA_W] = clipped[DATA_W-1:0];
                end
            end
        end
        merge_tensor = result;
    endfunction

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
            out_y <= '0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) out_y <= merge_tensor(in_x);
        end
    end
endmodule
