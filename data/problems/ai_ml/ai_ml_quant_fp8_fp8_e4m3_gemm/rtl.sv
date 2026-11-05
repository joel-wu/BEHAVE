module ai_ml_quant_fp8_fp8_e4m3_gemm (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [1:0]   in_layout,
    input  logic [31:0]  in_alpha,
    input  logic [31:0]  in_beta,
    input  logic [31:0]  in_a0,
    input  logic [31:0]  in_b0,
    input  logic [31:0]  in_c0,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_d0
);

    logic busy;
    logic [31:0] result_c;
    logic [7:0] a [0:3], b [0:3], c [0:3];
    logic [7:0] aa, bb, output_code;
    logic bad;
    logic signed [95:0] accumulator;
    integer r,j,k,i;

    assign in_ready = !busy || out_ready;
    assign out_valid = busy;

    function automatic logic signed [31:0] decode_scaled(input logic [7:0] code);
        logic signed [31:0] magnitude;
        begin
            if (code[6:3] == 0) magnitude = {29'd0,code[2:0]};
            else magnitude = (32'sd8 + $signed({1'b0,code[2:0]})) <<< (code[6:3]-1);
            decode_scaled = code[7] ? -magnitude : magnitude;
        end
    endfunction

    function automatic logic [7:0] encode_product(input logic signed [95:0] value);
        logic signed [95:0] magnitude, delta, distance, candidate;
        logic [6:0] best;
        integer code;
        begin
            magnitude = value < 0 ? -value : value;
            distance = magnitude;
            best = 0;
            for (code=1; code<127; code=code+1) begin
                candidate = decode_scaled(8'(code));
                delta = magnitude - (candidate <<< 9);
                if (delta < 0) delta = -delta;
                if (delta < distance || (delta == distance && (code & 1) == 0)) begin
                    best = 7'(code);
                    distance = delta;
                end
            end
            encode_product = {value[95],best};
        end
    endfunction

    always_comb begin
        result_c = 0;
        aa=0; bb=0; output_code=0; bad=0; accumulator=0;
        for (i=0; i<4; i=i+1) begin
            a[i] = in_a0[8*i +: 8];
            b[i] = in_b0[8*i +: 8];
            c[i] = in_c0[8*i +: 8];
        end
        for (r=0; r<2; r=r+1) begin
            for (j=0; j<2; j=j+1) begin
                bad = c[2*r+j][6:0] == 127;
                accumulator = $signed({1'b0,in_beta}) * decode_scaled(c[2*r+j]);
                accumulator = accumulator <<< 9;
                for (k=0; k<2; k=k+1) begin
                    aa = (in_layout==0 || in_layout==3) ? a[2*k+r] : a[2*r+k];
                    bb = in_layout==2 ? b[2*j+k] : b[2*k+j];
                    bad = bad || aa[6:0]==127 || bb[6:0]==127;
                    accumulator = accumulator + $signed({1'b0,in_alpha}) * decode_scaled(aa) * decode_scaled(bb);
                end
                output_code = bad ? 8'h7f : encode_product(accumulator);
                result_c[8*(2*r+j) +: 8] = output_code;
            end
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin busy<=0; out_d0<=0; end
        else if (in_valid && in_ready) begin busy<=1; out_d0<=result_c; end
        else if (out_ready) begin busy<=0; out_d0<=0; end
    end
endmodule
