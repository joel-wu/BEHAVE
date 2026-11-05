module red_black_stencil_point_update (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_zero_guess,
    input  logic [1:0]  in_phase_mode,
    input  logic        in_rb_phase,
    input  logic [1:0]  in_ndim,
    input  logic [2:0]  in_stencil_size,
    input  logic        in_constant_diag,
    input  logic [31:0] in_b,
    input  logic [31:0] in_xm,
    input  logic [31:0] in_xp,
    input  logic [31:0] in_ym,
    input  logic [31:0] in_yp,
    input  logic [31:0] in_zm,
    input  logic [31:0] in_zp,
    input  logic [31:0] in_app0,
    input  logic [31:0] in_app1,
    input  logic [31:0] in_app2,
    input  logic [31:0] in_app3,
    input  logic [31:0] in_app4,
    input  logic [31:0] in_app5,
    input  logic [31:0] in_ap_const,
    input  logic [31:0] in_ap_var,
    input  logic [15:0] in_coord_x,
    input  logic [15:0] in_coord_y,
    input  logic [15:0] in_coord_z,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_active,
    output logic [31:0] out_x_new,
    output logic        out_rb_phase_next
);

logic busy;
wire  output_consumed;
wire  input_accepted;

logic        calc_active;
logic [31:0] calc_x_new;
logic        calc_rb_phase_next;

logic [17:0] coord_sum;
logic signed [31:0] diag_s;
logic signed [31:0] b_s;
logic signed [31:0] app0_s, app1_s, app2_s, app3_s, app4_s, app5_s;
logic signed [31:0] xm_s, xp_s, ym_s, yp_s, zm_s, zp_s;

logic signed [63:0] prod0, prod1, prod2, prod3, prod4, prod5;
logic signed [65:0] neighbor_sum;
logic signed [65:0] numerator;
logic signed [65:0] b_ext;
logic signed [65:0] q_signed_tmp;
logic [65:0] aa_tmp;
logic [31:0] bb_tmp;
logic [65:0] q_abs_tmp;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [31:0] trunc_div_to_u32(
    input logic signed [65:0] a,
    input logic signed [31:0] b
);
    logic neg;
    logic signed [65:0] q_signed;
    logic [65:0] aa;
    logic [31:0] bb;
    logic [65:0] q_abs;
    begin
        trunc_div_to_u32 = 32'h00000000;
        q_signed = 66'sd0;
        aa = 66'd0;
        bb = 32'd0;
        q_abs = 66'd0;
        if (b == 32'sd0) begin
            trunc_div_to_u32 = 32'h00000000;
        end else begin
            neg = (a < 0) ^ (b < 0);
            if (a < 0)
                aa = -a;
            else
                aa = a;

            if (b < 0)
                bb = -b;
            else
                bb = b;

            q_abs = aa / bb;

            if (neg)
                q_signed = -$signed(q_abs);
            else
                q_signed = $signed(q_abs);

            trunc_div_to_u32 = q_signed[31:0];
        end
    end
endfunction

always_comb begin
    calc_active        = 1'b0;
    calc_x_new         = 32'h00000000;
    calc_rb_phase_next = in_rb_phase ^ 1'b1;

    coord_sum = 18'd0;
    diag_s = 32'sd0;
    b_s = 32'sd0;
    app0_s = 32'sd0;
    app1_s = 32'sd0;
    app2_s = 32'sd0;
    app3_s = 32'sd0;
    app4_s = 32'sd0;
    app5_s = 32'sd0;
    xm_s = 32'sd0;
    xp_s = 32'sd0;
    ym_s = 32'sd0;
    yp_s = 32'sd0;
    zm_s = 32'sd0;
    zp_s = 32'sd0;
    prod0 = 64'sd0;
    prod1 = 64'sd0;
    prod2 = 64'sd0;
    prod3 = 64'sd0;
    prod4 = 64'sd0;
    prod5 = 64'sd0;
    neighbor_sum = 66'sd0;
    numerator = 66'sd0;
    b_ext = 66'sd0;
    q_signed_tmp = 66'sd0;
    aa_tmp = 66'd0;
    bb_tmp = 32'd0;
    q_abs_tmp = 66'd0;

    if ((in_ndim == 2'b00) || (in_ndim == 2'b01)) begin
        coord_sum = {2'b00, in_coord_x};
    end else if (in_ndim == 2'b10) begin
        coord_sum = {2'b00, in_coord_x} + {2'b00, in_coord_y};
    end else begin
        coord_sum = {2'b00, in_coord_x} + {2'b00, in_coord_y} + {2'b00, in_coord_z};
    end

    calc_active = ((({17'd0, in_rb_phase} + coord_sum) & 18'd1) == 18'd0);

    diag_s = in_constant_diag ? $signed(in_ap_const) : $signed(in_ap_var);
    b_s    = $signed(in_b);
    b_ext  = {{34{b_s[31]}}, b_s};

    app0_s = $signed(in_app0);
    app1_s = $signed(in_app1);
    app2_s = $signed(in_app2);
    app3_s = $signed(in_app3);
    app4_s = $signed(in_app4);
    app5_s = $signed(in_app5);

    xm_s = $signed(in_xm);
    xp_s = $signed(in_xp);
    ym_s = $signed(in_ym);
    yp_s = $signed(in_yp);
    zm_s = $signed(in_zm);
    zp_s = $signed(in_zp);

    prod0 = app0_s * xm_s;
    prod1 = app1_s * xp_s;
    prod2 = app2_s * ym_s;
    prod3 = app3_s * yp_s;
    prod4 = app4_s * zm_s;
    prod5 = app5_s * zp_s;

    if (!calc_active) begin
        calc_x_new = 32'h00000000;
    end else if ((in_phase_mode & 2'b11) == 2'b00) begin
        calc_x_new = trunc_div_to_u32(b_ext, diag_s);
    end else begin
        if (in_stencil_size == 3'd5) begin
            neighbor_sum = $signed({{2{prod0[63]}}, prod0}) +
                           $signed({{2{prod1[63]}}, prod1}) +
                           $signed({{2{prod2[63]}}, prod2}) +
                           $signed({{2{prod3[63]}}, prod3});
        end else if (in_stencil_size == 3'd7) begin
            neighbor_sum = $signed({{2{prod0[63]}}, prod0}) +
                           $signed({{2{prod1[63]}}, prod1}) +
                           $signed({{2{prod2[63]}}, prod2}) +
                           $signed({{2{prod3[63]}}, prod3}) +
                           $signed({{2{prod4[63]}}, prod4}) +
                           $signed({{2{prod5[63]}}, prod5});
        end else begin
            neighbor_sum = $signed({{2{prod0[63]}}, prod0}) +
                           $signed({{2{prod1[63]}}, prod1});
        end

        numerator  = b_ext - neighbor_sum;
        calc_x_new = trunc_div_to_u32(numerator, diag_s);
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy              <= 1'b0;
        out_active        <= 1'b0;
        out_x_new         <= 32'h00000000;
        out_rb_phase_next <= 1'b0;
    end else if (input_accepted) begin
        busy              <= 1'b1;
        out_active        <= calc_active;
        out_x_new         <= calc_x_new;
        out_rb_phase_next <= calc_rb_phase_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule