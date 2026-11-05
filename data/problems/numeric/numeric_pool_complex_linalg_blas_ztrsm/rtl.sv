module complex_triangular_solve_2x2 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_side,
    input  logic        in_uplo,
    input  logic [1:0]  in_transa,
    input  logic        in_diag,

    input  logic signed [15:0] in_alpha_re,
    input  logic signed [15:0] in_alpha_im,

    input  logic signed [15:0] in_a00_re,
    input  logic signed [15:0] in_a00_im,
    input  logic signed [15:0] in_a01_re,
    input  logic signed [15:0] in_a01_im,
    input  logic signed [15:0] in_a10_re,
    input  logic signed [15:0] in_a10_im,
    input  logic signed [15:0] in_a11_re,
    input  logic signed [15:0] in_a11_im,

    input  logic signed [15:0] in_b00_re,
    input  logic signed [15:0] in_b00_im,
    input  logic signed [15:0] in_b01_re,
    input  logic signed [15:0] in_b01_im,
    input  logic signed [15:0] in_b10_re,
    input  logic signed [15:0] in_b10_im,
    input  logic signed [15:0] in_b11_re,
    input  logic signed [15:0] in_b11_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic signed [15:0] out_b00_re,
    output logic signed [15:0] out_b00_im,
    output logic signed [15:0] out_b01_re,
    output logic signed [15:0] out_b01_im,
    output logic signed [15:0] out_b10_re,
    output logic signed [15:0] out_b10_im,
    output logic signed [15:0] out_b11_re,
    output logic signed [15:0] out_b11_im
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;

    function automatic logic signed [15:0] wrap16(input logic signed [127:0] v);
        logic [127:0] tmp;
        begin
            tmp = v;
            wrap16 = tmp[15:0];
        end
    endfunction

    function automatic logic signed [127:0] trunc_div(
        input logic signed [127:0] n,
        input logic signed [127:0] d
    );
        logic signed [127:0] abs_n;
        logic signed [127:0] abs_d;
        logic signed [127:0] q;
        begin
            if (d == 0) begin
                trunc_div = 128'sd0;
            end else begin
                if (n < 0)
                    abs_n = -n;
                else
                    abs_n = n;

                if (d < 0)
                    abs_d = -d;
                else
                    abs_d = d;

                q = abs_n / abs_d;
                if ((n >= 0) == (d >= 0))
                    trunc_div = q;
                else
                    trunc_div = -q;
            end
        end
    endfunction

    function automatic logic signed [127:0] cdiv_re(
        input logic signed [127:0] nr,
        input logic signed [127:0] ni,
        input logic signed [127:0] dr,
        input logic signed [127:0] di
    );
        logic signed [127:0] den;
        logic signed [127:0] num;
        begin
            den = dr*dr + di*di;
            if (den == 0) begin
                cdiv_re = 128'sd0;
            end else begin
                num = nr*dr + ni*di;
                cdiv_re = trunc_div(num, den);
            end
        end
    endfunction

    function automatic logic signed [127:0] cdiv_im(
        input logic signed [127:0] nr,
        input logic signed [127:0] ni,
        input logic signed [127:0] dr,
        input logic signed [127:0] di
    );
        logic signed [127:0] den;
        logic signed [127:0] num;
        begin
            den = dr*dr + di*di;
            if (den == 0) begin
                cdiv_im = 128'sd0;
            end else begin
                num = ni*dr - nr*di;
                cdiv_im = trunc_div(num, den);
            end
        end
    endfunction

    always_ff @(posedge clk) begin
        logic conj;
        logic signed [127:0] alpha_re, alpha_im;
        logic signed [127:0] a00_re, a00_im, a01_re, a01_im, a10_re, a10_im, a11_re, a11_im;
        logic signed [127:0] b00r, b00i, b01r, b01i, b10r, b10i, b11r, b11i;
        logic signed [127:0] tr, ti, ur, ui;
        logic signed [127:0] offr, offi, diagr, diagi;
        logic alpha_zero, alpha_one;

        if (rst) begin
            busy       <= 1'b0;
            out_b00_re <= 16'sd0;
            out_b00_im <= 16'sd0;
            out_b01_re <= 16'sd0;
            out_b01_im <= 16'sd0;
            out_b10_re <= 16'sd0;
            out_b10_im <= 16'sd0;
            out_b11_re <= 16'sd0;
            out_b11_im <= 16'sd0;
        end else if (input_accepted) begin
            alpha_re = in_alpha_re; alpha_im = in_alpha_im;
            a00_re = in_a00_re; a00_im = in_a00_im;
            a01_re = in_a01_re; a01_im = in_a01_im;
            a10_re = in_a10_re; a10_im = in_a10_im;
            a11_re = in_a11_re; a11_im = in_a11_im;

            b00r = in_b00_re; b00i = in_b00_im;
            b01r = in_b01_re; b01i = in_b01_im;
            b10r = in_b10_re; b10i = in_b10_im;
            b11r = in_b11_re; b11i = in_b11_im;

            alpha_zero = (alpha_re == 0) && (alpha_im == 0);
            alpha_one  = (alpha_re == 1) && (alpha_im == 0);

            if (alpha_zero) begin
                b00r = 0; b00i = 0; b01r = 0; b01i = 0;
                b10r = 0; b10i = 0; b11r = 0; b11i = 0;
            end else if (in_side == 1'b0) begin
                if (in_transa == 2'd0) begin
                    if (in_uplo == 1'b0) begin
                        if (!alpha_one) begin
                            tr = alpha_re*b00r - alpha_im*b00i; ti = alpha_re*b00i + alpha_im*b00r; b00r = tr; b00i = ti;
                            tr = alpha_re*b10r - alpha_im*b10i; ti = alpha_re*b10i + alpha_im*b10r; b10r = tr; b10i = ti;
                        end
                        if (!((b10r == 0) && (b10i == 0))) begin
                            if (in_diag == 1'b1) begin
                                tr = cdiv_re(b10r, b10i, a11_re, a11_im);
                                ti = cdiv_im(b10r, b10i, a11_re, a11_im);
                                b10r = tr; b10i = ti;
                            end
                            tr = b10r*a01_re - b10i*a01_im;
                            ti = b10r*a01_im + b10i*a01_re;
                            b00r = b00r - tr; b00i = b00i - ti;
                        end
                        if (!((b00r == 0) && (b00i == 0))) begin
                            if (in_diag == 1'b1) begin
                                tr = cdiv_re(b00r, b00i, a00_re, a00_im);
                                ti = cdiv_im(b00r, b00i, a00_re, a00_im);
                                b00r = tr; b00i = ti;
                            end
                        end

                        if (!alpha_one) begin
                            tr = alpha_re*b01r - alpha_im*b01i; ti = alpha_re*b01i + alpha_im*b01r; b01r = tr; b01i = ti;
                            tr = alpha_re*b11r - alpha_im*b11i; ti = alpha_re*b11i + alpha_im*b11r; b11r = tr; b11i = ti;
                        end
                        if (!((b11r == 0) && (b11i == 0))) begin
                            if (in_diag == 1'b1) begin
                                tr = cdiv_re(b11r, b11i, a11_re, a11_im);
                                ti = cdiv_im(b11r, b11i, a11_re, a11_im);
                                b11r = tr; b11i = ti;
                            end
                            tr = b11r*a01_re - b11i*a01_im;
                            ti = b11r*a01_im + b11i*a01_re;
                            b01r = b01r - tr; b01i = b01i - ti;
                        end
                        if (!((b01r == 0) && (b01i == 0))) begin
                            if (in_diag == 1'b1) begin
                                tr = cdiv_re(b01r, b01i, a00_re, a00_im);
                                ti = cdiv_im(b01r, b01i, a00_re, a00_im);
                                b01r = tr; b01i = ti;
                            end
                        end
                    end else begin
                        if (!alpha_one) begin
                            tr = alpha_re*b00r - alpha_im*b00i; ti = alpha_re*b00i + alpha_im*b00r; b00r = tr; b00i = ti;
                            tr = alpha_re*b10r - alpha_im*b10i; ti = alpha_re*b10i + alpha_im*b10r; b10r = tr; b10i = ti;
                        end
                        if (!((b00r == 0) && (b00i == 0))) begin
                            if (in_diag == 1'b1) begin
                                tr = cdiv_re(b00r, b00i, a00_re, a00_im);
                                ti = cdiv_im(b00r, b00i, a00_re, a00_im);
                                b00r = tr; b00i = ti;
                            end
                            tr = b00r*a10_re - b00i*a10_im;
                            ti = b00r*a10_im + b00i*a10_re;
                            b10r = b10r - tr; b10i = b10i - ti;
                        end
                        if (!((b10r == 0) && (b10i == 0))) begin
                            if (in_diag == 1'b1) begin
                                tr = cdiv_re(b10r, b10i, a11_re, a11_im);
                                ti = cdiv_im(b10r, b10i, a11_re, a11_im);
                                b10r = tr; b10i = ti;
                            end
                        end

                        if (!alpha_one) begin
                            tr = alpha_re*b01r - alpha_im*b01i; ti = alpha_re*b01i + alpha_im*b01r; b01r = tr; b01i = ti;
                            tr = alpha_re*b11r - alpha_im*b11i; ti = alpha_re*b11i + alpha_im*b11r; b11r = tr; b11i = ti;
                        end
                        if (!((b01r == 0) && (b01i == 0))) begin
                            if (in_diag == 1'b1) begin
                                tr = cdiv_re(b01r, b01i, a00_re, a00_im);
                                ti = cdiv_im(b01r, b01i, a00_re, a00_im);
                                b01r = tr; b01i = ti;
                            end
                            tr = b01r*a10_re - b01i*a10_im;
                            ti = b01r*a10_im + b01i*a10_re;
                            b11r = b11r - tr; b11i = b11i - ti;
                        end
                        if (!((b11r == 0) && (b11i == 0))) begin
                            if (in_diag == 1'b1) begin
                                tr = cdiv_re(b11r, b11i, a11_re, a11_im);
                                ti = cdiv_im(b11r, b11i, a11_re, a11_im);
                                b11r = tr; b11i = ti;
                            end
                        end
                    end
                end else begin
                    conj = (in_transa != 2'd1);
                    if (in_uplo == 1'b0) begin
                        tr = alpha_re*b00r - alpha_im*b00i;
                        ti = alpha_re*b00i + alpha_im*b00r;
                        if (in_diag == 1'b1) begin
                            diagr = a00_re;
                            if (conj) diagi = -a00_im; else diagi = a00_im;
                            ur = cdiv_re(tr, ti, diagr, diagi);
                            ui = cdiv_im(tr, ti, diagr, diagi);
                            tr = ur; ti = ui;
                        end
                        b00r = tr; b00i = ti;

                        tr = alpha_re*b10r - alpha_im*b10i;
                        ti = alpha_re*b10i + alpha_im*b10r;
                        offr = a01_re;
                        if (conj) offi = -a01_im; else offi = a01_im;
                        ur = offr*b00r - offi*b00i;
                        ui = offr*b00i + offi*b00r;
                        tr = tr - ur; ti = ti - ui;
                        if (in_diag == 1'b1) begin
                            diagr = a11_re;
                            if (conj) diagi = -a11_im; else diagi = a11_im;
                            ur = cdiv_re(tr, ti, diagr, diagi);
                            ui = cdiv_im(tr, ti, diagr, diagi);
                            tr = ur; ti = ui;
                        end
                        b10r = tr; b10i = ti;

                        tr = alpha_re*b01r - alpha_im*b01i;
                        ti = alpha_re*b01i + alpha_im*b01r;
                        if (in_diag == 1'b1) begin
                            diagr = a00_re;
                            if (conj) diagi = -a00_im; else diagi = a00_im;
                            ur = cdiv_re(tr, ti, diagr, diagi);
                            ui = cdiv_im(tr, ti, diagr, diagi);
                            tr = ur; ti = ui;
                        end
                        b01r = tr; b01i = ti;

                        tr = alpha_re*b11r - alpha_im*b11i;
                        ti = alpha_re*b11i + alpha_im*b11r;
                        offr = a01_re;
                        if (conj) offi = -a01_im; else offi = a01_im;
                        ur = offr*b01r - offi*b01i;
                        ui = offr*b01i + offi*b01r;
                        tr = tr - ur; ti = ti - ui;
                        if (in_diag == 1'b1) begin
                            diagr = a11_re;
                            if (conj) diagi = -a11_im; else diagi = a11_im;
                            ur = cdiv_re(tr, ti, diagr, diagi);
                            ui = cdiv_im(tr, ti, diagr, diagi);
                            tr = ur; ti = ui;
                        end
                        b11r = tr; b11i = ti;
                    end else begin
                        tr = alpha_re*b10r - alpha_im*b10i;
                        ti = alpha_re*b10i + alpha_im*b10r;
                        if (in_diag == 1'b1) begin
                            diagr = a11_re;
                            if (conj) diagi = -a11_im; else diagi = a11_im;
                            ur = cdiv_re(tr, ti, diagr, diagi);
                            ui = cdiv_im(tr, ti, diagr, diagi);
                            tr = ur; ti = ui;
                        end
                        b10r = tr; b10i = ti;

                        tr = alpha_re*b00r - alpha_im*b00i;
                        ti = alpha_re*b00i + alpha_im*b00r;
                        offr = a10_re;
                        if (conj) offi = -a10_im; else offi = a10_im;
                        ur = offr*b10r - offi*b10i;
                        ui = offr*b10i + offi*b10r;
                        tr = tr - ur; ti = ti - ui;
                        if (in_diag == 1'b1) begin
                            diagr = a00_re;
                            if (conj) diagi = -a00_im; else diagi = a00_im;
                            ur = cdiv_re(tr, ti, diagr, diagi);
                            ui = cdiv_im(tr, ti, diagr, diagi);
                            tr = ur; ti = ui;
                        end
                        b00r = tr; b00i = ti;

                        tr = alpha_re*b11r - alpha_im*b11i;
                        ti = alpha_re*b11i + alpha_im*b11r;
                        if (in_diag == 1'b1) begin
                            diagr = a11_re;
                            if (conj) diagi = -a11_im; else diagi = a11_im;
                            ur = cdiv_re(tr, ti, diagr, diagi);
                            ui = cdiv_im(tr, ti, diagr, diagi);
                            tr = ur; ti = ui;
                        end
                        b11r = tr; b11i = ti;

                        tr = alpha_re*b01r - alpha_im*b01i;
                        ti = alpha_re*b01i + alpha_im*b01r;
                        offr = a10_re;
                        if (conj) offi = -a10_im; else offi = a10_im;
                        ur = offr*b11r - offi*b11i;
                        ui = offr*b11i + offi*b11r;
                        tr = tr - ur; ti = ti - ui;
                        if (in_diag == 1'b1) begin
                            diagr = a00_re;
                            if (conj) diagi = -a00_im; else diagi = a00_im;
                            ur = cdiv_re(tr, ti, diagr, diagi);
                            ui = cdiv_im(tr, ti, diagr, diagi);
                            tr = ur; ti = ui;
                        end
                        b01r = tr; b01i = ti;
                    end
                end
            end else begin
                if (in_transa == 2'd0) begin
                    if (in_uplo == 1'b0) begin
                        if (!alpha_one) begin
                            tr = alpha_re*b00r - alpha_im*b00i; ti = alpha_re*b00i + alpha_im*b00r; b00r = tr; b00i = ti;
                            tr = alpha_re*b10r - alpha_im*b10i; ti = alpha_re*b10i + alpha_im*b10r; b10r = tr; b10i = ti;
                        end
                        if (in_diag == 1'b1) begin
                            tr = cdiv_re(b00r, b00i, a00_re, a00_im); ti = cdiv_im(b00r, b00i, a00_re, a00_im); b00r=tr; b00i=ti;
                            tr = cdiv_re(b10r, b10i, a00_re, a00_im); ti = cdiv_im(b10r, b10i, a00_re, a00_im); b10r=tr; b10i=ti;
                        end
                        if (!alpha_one) begin
                            tr = alpha_re*b01r - alpha_im*b01i; ti = alpha_re*b01i + alpha_im*b01r; b01r = tr; b01i = ti;
                            tr = alpha_re*b11r - alpha_im*b11i; ti = alpha_re*b11i + alpha_im*b11r; b11r = tr; b11i = ti;
                        end
                        if (!((a01_re == 0) && (a01_im == 0))) begin
                            tr = a01_re*b00r - a01_im*b00i; ti = a01_re*b00i + a01_im*b00r; b01r = b01r - tr; b01i = b01i - ti;
                            tr = a01_re*b10r - a01_im*b10i; ti = a01_re*b10i + a01_im*b10r; b11r = b11r - tr; b11i = b11i - ti;
                        end
                        if (in_diag == 1'b1) begin
                            tr = cdiv_re(b01r, b01i, a11_re, a11_im); ti = cdiv_im(b01r, b01i, a11_re, a11_im); b01r=tr; b01i=ti;
                            tr = cdiv_re(b11r, b11i, a11_re, a11_im); ti = cdiv_im(b11r, b11i, a11_re, a11_im); b11r=tr; b11i=ti;
                        end
                    end else begin
                        if (!alpha_one) begin
                            tr = alpha_re*b01r - alpha_im*b01i; ti = alpha_re*b01i + alpha_im*b01r; b01r = tr; b01i = ti;
                            tr = alpha_re*b11r - alpha_im*b11i; ti = alpha_re*b11i + alpha_im*b11r; b11r = tr; b11i = ti;
                        end
                        if (in_diag == 1'b1) begin
                            tr = cdiv_re(b01r, b01i, a11_re, a11_im); ti = cdiv_im(b01r, b01i, a11_re, a11_im); b01r=tr; b01i=ti;
                            tr = cdiv_re(b11r, b11i, a11_re, a11_im); ti = cdiv_im(b11r, b11i, a11_re, a11_im); b11r=tr; b11i=ti;
                        end
                        if (!alpha_one) begin
                            tr = alpha_re*b00r - alpha_im*b00i; ti = alpha_re*b00i + alpha_im*b00r; b00r = tr; b00i = ti;
                            tr = alpha_re*b10r - alpha_im*b10i; ti = alpha_re*b10i + alpha_im*b10r; b10r = tr; b10i = ti;
                        end
                        if (!((a10_re == 0) && (a10_im == 0))) begin
                            tr = a10_re*b01r - a10_im*b01i; ti = a10_re*b01i + a10_im*b01r; b00r = b00r - tr; b00i = b00i - ti;
                            tr = a10_re*b11r - a10_im*b11i; ti = a10_re*b11i + a10_im*b11r; b10r = b10r - tr; b10i = b10i - ti;
                        end
                        if (in_diag == 1'b1) begin
                            tr = cdiv_re(b00r, b00i, a00_re, a00_im); ti = cdiv_im(b00r, b00i, a00_re, a00_im); b00r=tr; b00i=ti;
                            tr = cdiv_re(b10r, b10i, a00_re, a00_im); ti = cdiv_im(b10r, b10i, a00_re, a00_im); b10r=tr; b10i=ti;
                        end
                    end
                end else begin
                    conj = (in_transa != 2'd1);
                    if (in_uplo == 1'b0) begin
                        if (in_diag == 1'b1) begin
                            diagr = a11_re;
                            if (conj) diagi = -a11_im; else diagi = a11_im;
                            tr = cdiv_re(b01r, b01i, diagr, diagi); ti = cdiv_im(b01r, b01i, diagr, diagi); b01r=tr; b01i=ti;
                            tr = cdiv_re(b11r, b11i, diagr, diagi); ti = cdiv_im(b11r, b11i, diagr, diagi); b11r=tr; b11i=ti;
                        end
                        offr = a01_re;
                        if (conj) offi = -a01_im; else offi = a01_im;
                        if (!((offr == 0) && (offi == 0))) begin
                            tr = offr*b01r - offi*b01i; ti = offr*b01i + offi*b01r; b00r = b00r - tr; b00i = b00i - ti;
                            tr = offr*b11r - offi*b11i; ti = offr*b11i + offi*b11r; b10r = b10r - tr; b10i = b10i - ti;
                        end
                        if (!alpha_one) begin
                            tr = alpha_re*b01r - alpha_im*b01i; ti = alpha_re*b01i + alpha_im*b01r; b01r = tr; b01i = ti;
                            tr = alpha_re*b11r - alpha_im*b11i; ti = alpha_re*b11i + alpha_im*b11r; b11r = tr; b11i = ti;
                        end
                        if (in_diag == 1'b1) begin
                            diagr = a00_re;
                            if (conj) diagi = -a00_im; else diagi = a00_im;
                            tr = cdiv_re(b00r, b00i, diagr, diagi); ti = cdiv_im(b00r, b00i, diagr, diagi); b00r=tr; b00i=ti;
                            tr = cdiv_re(b10r, b10i, diagr, diagi); ti = cdiv_im(b10r, b10i, diagr, diagi); b10r=tr; b10i=ti;
                        end
                        if (!alpha_one) begin
                            tr = alpha_re*b00r - alpha_im*b00i; ti = alpha_re*b00i + alpha_im*b00r; b00r = tr; b00i = ti;
                            tr = alpha_re*b10r - alpha_im*b10i; ti = alpha_re*b10i + alpha_im*b10r; b10r = tr; b10i = ti;
                        end
                    end else begin
                        if (in_diag == 1'b1) begin
                            diagr = a00_re;
                            if (conj) diagi = -a00_im; else diagi = a00_im;
                            tr = cdiv_re(b00r, b00i, diagr, diagi); ti = cdiv_im(b00r, b00i, diagr, diagi); b00r=tr; b00i=ti;
                            tr = cdiv_re(b10r, b10i, diagr, diagi); ti = cdiv_im(b10r, b10i, diagr, diagi); b10r=tr; b10i=ti;
                        end
                        offr = a10_re;
                        if (conj) offi = -a10_im; else offi = a10_im;
                        if (!((offr == 0) && (offi == 0))) begin
                            tr = offr*b00r - offi*b00i; ti = offr*b00i + offi*b00r; b01r = b01r - tr; b01i = b01i - ti;
                            tr = offr*b10r - offi*b10i; ti = offr*b10i + offi*b10r; b11r = b11r - tr; b11i = b11i - ti;
                        end
                        if (!alpha_one) begin
                            tr = alpha_re*b00r - alpha_im*b00i; ti = alpha_re*b00i + alpha_im*b00r; b00r = tr; b00i = ti;
                            tr = alpha_re*b10r - alpha_im*b10i; ti = alpha_re*b10i + alpha_im*b10r; b10r = tr; b10i = ti;
                        end
                        if (in_diag == 1'b1) begin
                            diagr = a11_re;
                            if (conj) diagi = -a11_im; else diagi = a11_im;
                            tr = cdiv_re(b01r, b01i, diagr, diagi); ti = cdiv_im(b01r, b01i, diagr, diagi); b01r=tr; b01i=ti;
                            tr = cdiv_re(b11r, b11i, diagr, diagi); ti = cdiv_im(b11r, b11i, diagr, diagi); b11r=tr; b11i=ti;
                        end
                        if (!alpha_one) begin
                            tr = alpha_re*b01r - alpha_im*b01i; ti = alpha_re*b01i + alpha_im*b01r; b01r = tr; b01i = ti;
                            tr = alpha_re*b11r - alpha_im*b11i; ti = alpha_re*b11i + alpha_im*b11r; b11r = tr; b11i = ti;
                        end
                    end
                end
            end

            out_b00_re <= wrap16(b00r);
            out_b00_im <= wrap16(b00i);
            out_b01_re <= wrap16(b01r);
            out_b01_im <= wrap16(b01i);
            out_b10_re <= wrap16(b10r);
            out_b10_im <= wrap16(b10i);
            out_b11_re <= wrap16(b11r);
            out_b11_im <= wrap16(b11i);
            busy       <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
