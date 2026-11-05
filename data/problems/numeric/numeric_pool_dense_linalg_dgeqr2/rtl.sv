module householder_qr2x2_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a00,
    input  logic [31:0] in_a01,
    input  logic [31:0] in_a10,
    input  logic [31:0] in_a11,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_a00,
    output logic [31:0] out_a01,
    output logic [31:0] out_a10,
    output logic [31:0] out_a11,
    output logic [31:0] out_tau0,
    output logic [31:0] out_tau1
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [63:0] abs64(input logic signed [63:0] v);
        begin
            if (v < 0)
                abs64 = -v;
            else
                abs64 = v;
        end
    endfunction

    function automatic logic [127:0] abs128(input logic signed [127:0] v);
        begin
            if (v < 0)
                abs128 = -v;
            else
                abs128 = v;
        end
    endfunction

    function automatic logic signed [63:0] trunc_q16_mul64(
        input logic signed [63:0] a,
        input logic signed [63:0] b
    );
        logic signed [127:0] p;
        begin
            p = a * b;
            trunc_q16_mul64 = p >>> 16;
        end
    endfunction

    function automatic logic signed [63:0] trunc_q16_div64(
        input logic signed [63:0] a,
        input logic signed [63:0] b
    );
        logic signed [127:0] n;
        logic [127:0] an;
        logic [63:0]  ab;
        logic [127:0] q;
        begin
            trunc_q16_div64 = 64'sd0;
            if (b != 0) begin
                n = a <<< 16;
                an = abs128(n);
                ab = abs64(b);
                q = an / ab;
                if ((n < 0) ^ (b < 0))
                    trunc_q16_div64 = -$signed(q[63:0]);
                else
                    trunc_q16_div64 =  $signed(q[63:0]);
            end
        end
    endfunction

    function automatic logic [63:0] norm_q16_2_32(
        input logic signed [31:0] x,
        input logic signed [31:0] y
    );
        logic [63:0] ux;
        logic [63:0] uy;
        logic [63:0] s_sum;
        logic [79:0] target;
        logic [63:0] r;
        logic [63:0] t;
        logic [127:0] tt;
        integer i;
        begin
            ux = abs64({{32{x[31]}}, x});
            uy = abs64({{32{y[31]}}, y});
            s_sum = ux * ux + uy * uy;
            target = {16'd0, s_sum};
            r = 64'd0;
            for (i = 31; i >= 0; i = i - 1) begin
                t = r | (64'd1 << i);
                tt = t * t;
                if (tt <= {{48{1'b0}}, target})
                    r = t;
            end
            norm_q16_2_32 = r;
        end
    endfunction

    logic signed [31:0] x0_s;
    logic signed [31:0] x1_s;
    logic signed [31:0] y0_s;
    logic signed [31:0] y1_s;

    logic [63:0] norm0_u;
    logic signed [63:0] norm0_s;
    logic signed [63:0] beta0_s;
    logic signed [63:0] u0_s;
    logic signed [63:0] v10_s;
    logic signed [63:0] tau0_s;
    logic signed [63:0] r00_s;
    logic signed [63:0] dot0_s;
    logic signed [63:0] w0_s;
    logic signed [63:0] r01_s;
    logic signed [63:0] c11_s;
    logic signed [63:0] r11_s;
    logic signed [63:0] tau1_s;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy     <= 1'b0;
            out_a00  <= 32'h00000000;
            out_a01  <= 32'h00000000;
            out_a10  <= 32'h00000000;
            out_a11  <= 32'h00000000;
            out_tau0 <= 32'h00000000;
            out_tau1 <= 32'h00000000;
        end else if (input_accepted) begin
            x0_s = $signed(in_a00);
            x1_s = $signed(in_a01);
            y0_s = $signed(in_a10);
            y1_s = $signed(in_a11);

            norm0_u = norm_q16_2_32(x0_s, y0_s);
            norm0_s = $signed(norm0_u);

            if (norm0_u == 64'd0)
                beta0_s = 64'sd0;
            else if (x0_s < 0)
                beta0_s = norm0_s;
            else
                beta0_s = -norm0_s;

            if (y0_s == 0) begin
                tau0_s = 64'sd0;
                v10_s  = 64'sd0;
                r00_s  = {{32{x0_s[31]}}, x0_s};
            end else begin
                u0_s   = {{32{x0_s[31]}}, x0_s} - beta0_s;
                v10_s  = trunc_q16_div64({{32{y0_s[31]}}, y0_s}, u0_s);
                tau0_s = trunc_q16_div64(beta0_s - {{32{x0_s[31]}}, x0_s}, beta0_s);
                r00_s  = beta0_s;
            end

            if (tau0_s == 0) begin
                r01_s = {{32{x1_s[31]}}, x1_s};
                c11_s = {{32{y1_s[31]}}, y1_s};
            end else begin
                dot0_s = {{32{x1_s[31]}}, x1_s} + trunc_q16_mul64(v10_s, {{32{y1_s[31]}}, y1_s});
                w0_s   = trunc_q16_mul64(tau0_s, dot0_s);
                r01_s  = {{32{x1_s[31]}}, x1_s} - w0_s;
                c11_s  = {{32{y1_s[31]}}, y1_s} - trunc_q16_mul64(v10_s, w0_s);
            end

            r11_s  = c11_s;
            tau1_s = 64'sd0;

            out_a00  <= r00_s[31:0];
            out_a01  <= r01_s[31:0];
            out_a10  <= v10_s[31:0];
            out_a11  <= r11_s[31:0];
            out_tau0 <= tau0_s[31:0];
            out_tau1 <= tau1_s[31:0];
            busy     <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
