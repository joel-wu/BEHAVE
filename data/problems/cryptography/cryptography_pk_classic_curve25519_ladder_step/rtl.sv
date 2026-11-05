module cryptography_pk_classic_curve25519_ladder_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_x2,
    input  logic [255:0] in_z2,
    input  logic [255:0] in_x3,
    input  logic [255:0] in_z3,
    input  logic [255:0] in_xp,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_x2,
    output logic [255:0] out_z2,
    output logic [255:0] out_x3,
    output logic [255:0] out_z3
);

    localparam logic [254:0] P254 = 255'h7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffed;
    localparam logic [255:0] P256 = {1'b0, P254};
    localparam logic [255:0] A24  = 256'd121666;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [255:0] mod_p_256(input logic [255:0] x);
        logic [255:0] tmp;
        begin
            if (x[255] == 1'b0) begin
                tmp = x;
                if (tmp >= P256) begin
                    mod_p_256 = tmp - P256;
                end else begin
                    mod_p_256 = tmp;
                end
            end else begin
                tmp = {1'b0, x[254:0]};
                tmp = tmp + 256'd19;
                if (tmp >= P256) begin
                    mod_p_256 = tmp - P256;
                end else begin
                    mod_p_256 = tmp;
                end
            end
        end
    endfunction

    function automatic logic [255:0] add_mod_p(input logic [255:0] a, input logic [255:0] b);
        logic [256:0] sum_ext;
        logic [255:0] folded;
        begin
            sum_ext = {1'b0, a} + {1'b0, b};
            folded  = sum_ext[255:0];
            if (sum_ext[256]) begin
                folded = folded + 256'd38;
            end
            if (folded >= P256) begin
                add_mod_p = folded - P256;
            end else begin
                add_mod_p = folded;
            end
        end
    endfunction

    function automatic logic [255:0] sub_mod_p(input logic [255:0] a, input logic [255:0] b);
        begin
            if (a >= b) begin
                sub_mod_p = a - b;
            end else begin
                sub_mod_p = a + P256 - b;
            end
        end
    endfunction

    function automatic logic [255:0] mul_mod_p(input logic [255:0] a, input logic [255:0] b);
        logic [511:0] prod;
        logic [254:0] lo1;
        logic [256:0] hi1;
        logic [261:0] s1;
        logic [254:0] lo2;
        logic [6:0]   hi2;
        logic [261:0] s2;
        logic [255:0] r;
        begin
            prod = a * b;

            lo1 = prod[254:0];
            hi1 = prod[511:255];
            s1  = {7'b0, lo1} + ({5'b0, hi1} * 6'd19);

            lo2 = s1[254:0];
            hi2 = s1[261:255];
            s2  = {7'b0, lo2} + ({255'b0, hi2} * 6'd19);

            r = s2[255:0];
            if (r >= P256) begin
                r = r - P256;
            end
            if (r >= P256) begin
                r = r - P256;
            end
            mul_mod_p = r;
        end
    endfunction

    logic [255:0] x2_r, z2_r, x3_r, z3_r, xp_r;
    logic [255:0] d_r, b_r, a_r, c_r;
    logic [255:0] da_r, cb_r;
    logic [255:0] x5_pre_r, z5_pre_r;
    logic [255:0] x5_r, t_r, bb_r, aa_r, e_r;
    logic [255:0] z5_r, u_r, v_r, z4_r, x4_r;

    logic [255:0] x2_n, z2_n, x3_n, z3_n, xp_n;
    logic [255:0] d_n, b_n, a_n, c_n;
    logic [255:0] da_n, cb_n;
    logic [255:0] x5_pre_n, z5_pre_n;
    logic [255:0] x5_n, t_n, bb_n, aa_n, e_n;
    logic [255:0] z5_n, u_n, v_n, z4_n, x4_n;

    always_comb begin
        x2_n = mod_p_256(in_x2);
        z2_n = mod_p_256(in_z2);
        x3_n = mod_p_256(in_x3);
        z3_n = mod_p_256(in_z3);
        xp_n = mod_p_256(in_xp);

        d_n      = sub_mod_p(x3_n, z3_n);
        b_n      = sub_mod_p(x2_n, z2_n);
        a_n      = add_mod_p(x2_n, z2_n);
        c_n      = add_mod_p(x3_n, z3_n);
        da_n     = mul_mod_p(d_n, a_n);
        cb_n     = mul_mod_p(c_n, b_n);
        x5_pre_n = add_mod_p(da_n, cb_n);
        z5_pre_n = sub_mod_p(da_n, cb_n);
        x5_n     = mul_mod_p(x5_pre_n, x5_pre_n);
        t_n      = mul_mod_p(z5_pre_n, z5_pre_n);
        bb_n     = mul_mod_p(b_n, b_n);
        aa_n     = mul_mod_p(a_n, a_n);
        e_n      = sub_mod_p(aa_n, bb_n);
        z5_n     = mul_mod_p(xp_n, t_n);
        u_n      = mul_mod_p(A24, e_n);
        v_n      = add_mod_p(bb_n, u_n);
        z4_n     = mul_mod_p(e_n, v_n);
        x4_n     = mul_mod_p(aa_n, bb_n);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy   <= 1'b0;
            out_x2 <= 256'd0;
            out_z2 <= 256'd0;
            out_x3 <= 256'd0;
            out_z3 <= 256'd0;

            x2_r <= 256'd0;
            z2_r <= 256'd0;
            x3_r <= 256'd0;
            z3_r <= 256'd0;
            xp_r <= 256'd0;
            d_r  <= 256'd0;
            b_r  <= 256'd0;
            a_r  <= 256'd0;
            c_r  <= 256'd0;
            da_r <= 256'd0;
            cb_r <= 256'd0;
            x5_pre_r <= 256'd0;
            z5_pre_r <= 256'd0;
            x5_r <= 256'd0;
            t_r  <= 256'd0;
            bb_r <= 256'd0;
            aa_r <= 256'd0;
            e_r  <= 256'd0;
            z5_r <= 256'd0;
            u_r  <= 256'd0;
            v_r  <= 256'd0;
            z4_r <= 256'd0;
            x4_r <= 256'd0;
        end else if (input_accepted) begin
            busy   <= 1'b1;

            x2_r <= x2_n;
            z2_r <= z2_n;
            x3_r <= x3_n;
            z3_r <= z3_n;
            xp_r <= xp_n;
            d_r  <= d_n;
            b_r  <= b_n;
            a_r  <= a_n;
            c_r  <= c_n;
            da_r <= da_n;
            cb_r <= cb_n;
            x5_pre_r <= x5_pre_n;
            z5_pre_r <= z5_pre_n;
            x5_r <= x5_n;
            t_r  <= t_n;
            bb_r <= bb_n;
            aa_r <= aa_n;
            e_r  <= e_n;
            z5_r <= z5_n;
            u_r  <= u_n;
            v_r  <= v_n;
            z4_r <= z4_n;
            x4_r <= x4_n;

            out_x2 <= x4_n;
            out_z2 <= z4_n;
            out_x3 <= x5_n;
            out_z3 <= z5_n;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule