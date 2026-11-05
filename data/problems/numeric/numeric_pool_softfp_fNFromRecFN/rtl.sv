module recfn_to_fn (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [32:0] in_recfn,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_fn
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    logic        sign;
    logic [8:0]  rec_exp;
    logic [22:0] rec_sig;
    logic        is_nan;
    logic        is_inf;
    logic        is_zero;
    logic [8:0]  s_exp;
    logic [23:0] sig;
    logic        is_subnormal;
    logic [4:0]  shift;
    logic [22:0] shifted;
    logic [22:0] denorm_fract;
    logic [7:0]  exp_out;
    logic [7:0]  exp_field;
    logic [22:0] fract_field;
    logic [31:0] out_fn_next;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        sign = in_recfn[32];
        rec_exp = in_recfn[31:23];
        rec_sig = in_recfn[22:0];

        is_nan = (rec_exp[8:6] == 3'b111);
        is_inf = (rec_exp[8:6] == 3'b110);
        is_zero = (rec_exp[8:6] == 3'b000);

        s_exp = rec_exp;
        sig = {!is_zero, rec_sig};

        is_subnormal = (s_exp < 9'd130);

        shift = 5'd1 - rec_exp[4:0];
        shifted = sig[23:1];

        if (shift >= 5'd23) begin
            denorm_fract = 23'h000000;
        end else begin
            denorm_fract = shifted >> shift;
        end

        if (is_subnormal) begin
            exp_out = 8'h00;
        end else begin
            exp_out = rec_exp[7:0] - 8'd129;
        end

        exp_field = exp_out | ({8{is_nan | is_inf}});

        if (is_subnormal) begin
            fract_field = denorm_fract;
        end else begin
            fract_field = rec_sig & {23{~is_inf}};
        end

        out_fn_next = {sign, exp_field, fract_field};
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy   <= 1'b0;
            out_fn <= 32'h00000000;
        end else if (input_accepted) begin
            busy   <= 1'b1;
            out_fn <= out_fn_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
