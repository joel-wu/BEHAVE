module chebyshev_distance_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_a_base,
    input  logic [7:0]  in_b_base,
    input  logic [7:0]  in_block_size,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_distance,

    output logic        vec_a_req_valid,
    output logic [7:0]  vec_a_req_addr,
    input  logic        vec_a_req_ready,
    input  logic        vec_a_resp_valid,
    input  logic [31:0] vec_a_resp_rdata,

    output logic        vec_b_req_valid,
    output logic [7:0]  vec_b_req_addr,
    input  logic        vec_b_req_ready,
    input  logic        vec_b_resp_valid,
    input  logic [31:0] vec_b_resp_rdata
);

    typedef enum logic [2:0] {
        ST_IDLE      = 3'd0,
        ST_REQ_A     = 3'd1,
        ST_WAIT_A    = 3'd2,
        ST_REQ_B     = 3'd3,
        ST_WAIT_B    = 3'd4,
        ST_NEXT_OR_O = 3'd5,
        ST_OUT       = 3'd6
    } state_t;

    state_t state;

    logic [7:0]  base_a_reg;
    logic [7:0]  base_b_reg;
    logic [7:0]  block_size_reg;
    logic [7:0]  index_reg;
    logic [31:0] a_data_reg;
    logic [31:0] running_max_reg;
    logic        first_elem_reg;

    logic [31:0] sub_result;
    logic [31:0] abs_result;
    logic signed [1:0] cmp_result;
    logic [31:0] next_running_max;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = (state == ST_IDLE);
    assign out_valid = (state == ST_OUT);

    assign vec_a_req_valid = (state == ST_REQ_A);
    assign vec_b_req_valid = (state == ST_REQ_B);
    assign vec_a_req_addr  = base_a_reg + index_reg;
    assign vec_b_req_addr  = base_b_reg + index_reg;

    assign next_running_max = first_elem_reg ? abs_result : ((cmp_result > 0) ? abs_result : running_max_reg);

    fp32_sub u_sub (
        .a(a_data_reg),
        .b(vec_b_resp_rdata),
        .result(sub_result)
    );

    assign abs_result = {1'b0, sub_result[30:0]};

    fp32_cmp_unit u_cmp (
        .a(abs_result),
        .b(running_max_reg),
        .cmp(cmp_result)
    );

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            state           <= ST_IDLE;
            base_a_reg      <= 8'h00;
            base_b_reg      <= 8'h00;
            block_size_reg  <= 8'h00;
            index_reg       <= 8'h00;
            a_data_reg      <= 32'h00000000;
            running_max_reg <= 32'h00000000;
            first_elem_reg  <= 1'b1;
            out_distance    <= 32'h00000000;
        end else begin
            case (state)
                ST_IDLE: begin
                    if (input_accepted) begin
                        base_a_reg      <= in_a_base;
                        base_b_reg      <= in_b_base;
                        block_size_reg  <= in_block_size;
                        index_reg       <= 8'h00;
                        a_data_reg      <= 32'h00000000;
                        running_max_reg <= 32'h00000000;
                        first_elem_reg  <= 1'b1;
                        if (in_block_size == 8'h00) begin
                            out_distance <= 32'h00000000;
                            state        <= ST_OUT;
                        end else begin
                            state <= ST_REQ_A;
                        end
                    end
                end

                ST_REQ_A: begin
                    if (vec_a_req_ready) begin
                        state <= ST_WAIT_A;
                    end
                end

                ST_WAIT_A: begin
                    if (vec_a_resp_valid) begin
                        a_data_reg <= vec_a_resp_rdata;
                        state      <= ST_REQ_B;
                    end
                end

                ST_REQ_B: begin
                    if (vec_b_req_ready) begin
                        state <= ST_WAIT_B;
                    end
                end

                ST_WAIT_B: begin
                    if (vec_b_resp_valid) begin
                        running_max_reg <= next_running_max;
                        first_elem_reg  <= 1'b0;
                        if ((index_reg + 8'd1) >= block_size_reg) begin
                            out_distance <= next_running_max;
                            state        <= ST_OUT;
                        end else begin
                            state <= ST_NEXT_OR_O;
                        end
                    end
                end

                ST_NEXT_OR_O: begin
                    index_reg <= index_reg + 8'd1;
                    state     <= ST_REQ_A;
                end

                ST_OUT: begin
                    if (output_consumed) begin
                        state <= ST_IDLE;
                    end
                end

                default: begin
                    state <= ST_IDLE;
                end
            endcase
        end
    end

endmodule


module fp32_sub (
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic [31:0] result
);
function automatic logic [63:0] fp_shift_jam(input logic [63:0] x, input integer n);
    if (n <= 0) fp_shift_jam = x;
    else if (n >= 64) fp_shift_jam = {63'b0, |x};
    else fp_shift_jam = (x >> n) | {63'b0, |(x << (64-n))};
endfunction

function automatic logic [31:0] fp_round_pack(
    input logic sign_bit, input integer exponent, input logic [63:0] sig
);
    integer e;
    logic [63:0] shifted, rounded;
    begin
        e = exponent;
        shifted = sig;
        if (e < 1) begin
            shifted = fp_shift_jam(shifted, 1-e);
            e = 1;
        end
        rounded = (shifted >> 3) + {63'b0, (shifted[2] &&
                  (shifted[1] || shifted[0] || shifted[3]))};
        if (rounded[24]) begin
            rounded = rounded >> 1;
            e = e+1;
        end
        if (e >= 255) fp_round_pack = {sign_bit, 8'hff, 23'b0};
        else if (rounded < 64'h800000)
            fp_round_pack = {sign_bit, 8'b0, rounded[22:0]};
        else fp_round_pack = {sign_bit, e[7:0], rounded[22:0]};
    end
endfunction

function automatic logic [31:0] fp32_add_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sa, sb, sign_bit;
    logic [63:0] ma, mb, hi, lo, sum;
    begin
        sa = a[31]; sb = b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
            fp32_add_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000) && (sa != sb))
            fp32_add_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_add_bits = a;
        else if (b[30:0] == 31'h7f800000) fp32_add_bits = b;
        else if ((a[30:0] == 0) && (b[30:0] == 0))
            fp32_add_bits = {sa & sb, 31'b0};
        else begin
            if ((ea > eb) || ((ea == eb) && (ma >= mb))) begin
                hi = ma << 3; lo = fp_shift_jam(mb << 3, ea-eb);
                sign_bit = sa; e = ea;
            end else begin
                hi = mb << 3; lo = fp_shift_jam(ma << 3, eb-ea);
                sign_bit = sb; e = eb;
            end
            sum = sa == sb ? hi+lo : hi-lo;
            if (sum == 0) fp32_add_bits = 32'b0;
            else begin
                if (sum[27]) begin
                    sum = fp_shift_jam(sum, 1); e = e+1;
                end
                for (i=0; i<26; i=i+1) begin
                    if (!sum[26] && e>1) begin sum=sum<<1; e=e-1; end
                end
                fp32_add_bits = fp_round_pack(sign_bit, e, sum);
            end
        end
    end
endfunction
assign result = fp32_add_bits(a, b ^ 32'h80000000);
endmodule


module fp32_add (
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic [31:0] result
);
function automatic logic [63:0] fp_shift_jam(input logic [63:0] x, input integer n);
    if (n <= 0) fp_shift_jam = x;
    else if (n >= 64) fp_shift_jam = {63'b0, |x};
    else fp_shift_jam = (x >> n) | {63'b0, |(x << (64-n))};
endfunction

function automatic logic [31:0] fp_round_pack(
    input logic sign_bit, input integer exponent, input logic [63:0] sig
);
    integer e;
    logic [63:0] shifted, rounded;
    begin
        e = exponent;
        shifted = sig;
        if (e < 1) begin
            shifted = fp_shift_jam(shifted, 1-e);
            e = 1;
        end
        rounded = (shifted >> 3) + {63'b0, (shifted[2] &&
                  (shifted[1] || shifted[0] || shifted[3]))};
        if (rounded[24]) begin
            rounded = rounded >> 1;
            e = e+1;
        end
        if (e >= 255) fp_round_pack = {sign_bit, 8'hff, 23'b0};
        else if (rounded < 64'h800000)
            fp_round_pack = {sign_bit, 8'b0, rounded[22:0]};
        else fp_round_pack = {sign_bit, e[7:0], rounded[22:0]};
    end
endfunction

function automatic logic [31:0] fp32_add_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sa, sb, sign_bit;
    logic [63:0] ma, mb, hi, lo, sum;
    begin
        sa = a[31]; sb = b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
            fp32_add_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000) && (sa != sb))
            fp32_add_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_add_bits = a;
        else if (b[30:0] == 31'h7f800000) fp32_add_bits = b;
        else if ((a[30:0] == 0) && (b[30:0] == 0))
            fp32_add_bits = {sa & sb, 31'b0};
        else begin
            if ((ea > eb) || ((ea == eb) && (ma >= mb))) begin
                hi = ma << 3; lo = fp_shift_jam(mb << 3, ea-eb);
                sign_bit = sa; e = ea;
            end else begin
                hi = mb << 3; lo = fp_shift_jam(ma << 3, eb-ea);
                sign_bit = sb; e = eb;
            end
            sum = sa == sb ? hi+lo : hi-lo;
            if (sum == 0) fp32_add_bits = 32'b0;
            else begin
                if (sum[27]) begin
                    sum = fp_shift_jam(sum, 1); e = e+1;
                end
                for (i=0; i<26; i=i+1) begin
                    if (!sum[26] && e>1) begin sum=sum<<1; e=e-1; end
                end
                fp32_add_bits = fp_round_pack(sign_bit, e, sum);
            end
        end
    end
endfunction
assign result = fp32_add_bits(a, b);
endmodule


module fp32_cmp_unit (
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic signed [1:0] cmp
);
function automatic logic signed [1:0] fp32_cmp_bits(input logic [31:0] a, b);
    if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
        fp32_cmp_bits = 2'sb10; // -2: unordered, never an ordered <= result.
    else if ((a == b) || ((a[30:0] == 0) && (b[30:0] == 0))) fp32_cmp_bits = 0;
    else if (a[31] != b[31]) fp32_cmp_bits = a[31] ? -2'sd1 : 2'sd1;
    else if ((a[30:0] < b[30:0]) != a[31]) fp32_cmp_bits = -2'sd1;
    else fp32_cmp_bits = 2'sd1;
endfunction
assign cmp = fp32_cmp_bits(a,b);
endmodule
