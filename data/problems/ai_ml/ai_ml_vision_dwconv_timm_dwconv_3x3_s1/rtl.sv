module ai_ml_vision_dwconv_timm_dwconv_3x3_s1 (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [287:0] in_patch, in_kernel,
    input logic [127:0] in_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [127:0] out_y
);
    logic [127:0] next_y;
    logic signed [63:0] total;
    logic signed [7:0] x, w;
    assign in_ready = !out_valid || out_ready;
    always_comb begin
        next_y = '0;
        total = 0;
        x = 0;
        w = 0;
        for (int c=0; c<4; c++) begin
            total = $signed(in_bias[c*32 +: 32]);
            for (int k=0; k<9; k++) begin
                x = $signed(in_patch[(c*9+k)*8 +: 8]);
                w = $signed(in_kernel[(c*9+k)*8 +: 8]);
                total = total + x*w;
            end
            next_y[c*32 +: 32] = total[31:0];
        end
    end
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            out_valid <= 0;
            out_y <= 0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) out_y <= next_y;
            else out_y <= 0;
        end
    end
endmodule
