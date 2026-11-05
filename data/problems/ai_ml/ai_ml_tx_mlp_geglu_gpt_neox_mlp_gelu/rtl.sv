module ai_ml_tx_mlp_geglu_gpt_neox_mlp_gelu #(
    parameter int HIDDEN_SIZE = 4,
    parameter int INTERMEDIATE_SIZE = 4,
    parameter int DATA_W = 16,
    parameter int FRAC_W = 12
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic signed [HIDDEN_SIZE*DATA_W-1:0] in_hidden_states,
    input  logic signed [HIDDEN_SIZE*INTERMEDIATE_SIZE*DATA_W-1:0] in_w1,
    input  logic signed [INTERMEDIATE_SIZE*DATA_W-1:0] in_b1,
    input  logic signed [HIDDEN_SIZE*INTERMEDIATE_SIZE*DATA_W-1:0] in_w2,
    input  logic signed [HIDDEN_SIZE*DATA_W-1:0] in_b2,

    output logic out_valid,
    input  logic out_ready,
    output logic signed [HIDDEN_SIZE*DATA_W-1:0] out_hidden_states
);

    localparam int ACC_W = 4*DATA_W + 2*FRAC_W + 8;

    logic busy;
    logic signed [HIDDEN_SIZE*DATA_W-1:0] out_hidden_states_next;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic signed [DATA_W-1:0] sat_data(
        input logic signed [ACC_W-1:0] v
    );
        logic signed [ACC_W-1:0] lo_ext;
        logic signed [ACC_W-1:0] hi_ext;
        begin
            lo_ext = -((ACC_W'(1)) <<< (DATA_W - 1));
            hi_ext =  (((ACC_W'(1)) <<< (DATA_W - 1)) - 1);
            if (v < lo_ext) begin
                sat_data = lo_ext[DATA_W-1:0];
            end else if (v > hi_ext) begin
                sat_data = hi_ext[DATA_W-1:0];
            end else begin
                sat_data = v[DATA_W-1:0];
            end
        end
    endfunction

    function automatic logic signed [ACC_W-1:0] div_trunc_zero(
        input logic signed [ACC_W-1:0] n,
        input logic signed [ACC_W-1:0] d
    );
        logic signed [ACC_W-1:0] abs_n;
        logic signed [ACC_W-1:0] q_abs;
        begin
            abs_n = '0;
            q_abs = '0;
            if (d == '0) begin
                div_trunc_zero = '0;
            end else begin
                if (n >= '0) begin
                    div_trunc_zero = n / d;
                end else begin
                    abs_n = -n;
                    q_abs = abs_n / d;
                    div_trunc_zero = -q_abs;
                end
            end
        end
    endfunction

    function automatic logic signed [ACC_W-1:0] qmul_acc(
        input logic signed [ACC_W-1:0] a,
        input logic signed [ACC_W-1:0] b
    );
        logic signed [2*ACC_W-1:0] prod_wide;
        begin
            prod_wide = a * b;
            qmul_acc = prod_wide >>> FRAC_W;
        end
    endfunction

    integer i;
    integer j;
    integer k;

    logic signed [DATA_W-1:0] x      [0:HIDDEN_SIZE-1];
    logic signed [DATA_W-1:0] b1     [0:INTERMEDIATE_SIZE-1];
    logic signed [DATA_W-1:0] b2     [0:HIDDEN_SIZE-1];
    logic signed [DATA_W-1:0] w1     [0:INTERMEDIATE_SIZE-1][0:HIDDEN_SIZE-1];
    logic signed [DATA_W-1:0] w2     [0:HIDDEN_SIZE-1][0:INTERMEDIATE_SIZE-1];
    logic signed [DATA_W-1:0] pre_gelu [0:INTERMEDIATE_SIZE-1];
    logic signed [DATA_W-1:0] act      [0:INTERMEDIATE_SIZE-1];
    logic signed [DATA_W-1:0] y        [0:HIDDEN_SIZE-1];

    logic signed [ACC_W-1:0] q_acc;
    logic signed [ACC_W-1:0] one_acc;
    logic signed [ACC_W-1:0] c_acc;
    logic signed [ACC_W-1:0] s_acc;

    logic signed [ACC_W-1:0] acc1;
    logic signed [ACC_W-1:0] acc2;
    logic signed [ACC_W-1:0] xg;
    logic signed [ACC_W-1:0] x2;
    logic signed [ACC_W-1:0] x3;
    logic signed [ACC_W-1:0] u;
    logic signed [ACC_W-1:0] v;
    logic signed [ACC_W-1:0] v2;
    logic signed [ACC_W-1:0] num;
    logic signed [ACC_W-1:0] den;
    logic signed [ACC_W-1:0] t;
    logic signed [ACC_W-1:0] g;
    logic signed [ACC_W-1:0] h;

    always_comb begin
        q_acc   = FRAC_W;
        one_acc = (ACC_W'(1)) <<< FRAC_W;
        c_acc   = ((ACC_W'(187548) <<< FRAC_W) + (ACC_W'(1) <<< 21)) >>> 22;
        s_acc   = ((ACC_W'(3346570) <<< FRAC_W) + (ACC_W'(1) <<< 21)) >>> 22;
        out_hidden_states_next = '0;

        for (i = 0; i < HIDDEN_SIZE; i = i + 1) begin
            x[i] = in_hidden_states[i*DATA_W +: DATA_W];
            b2[i] = in_b2[i*DATA_W +: DATA_W];
            y[i] = '0;
        end

        for (i = 0; i < INTERMEDIATE_SIZE; i = i + 1) begin
            b1[i] = in_b1[i*DATA_W +: DATA_W];
            pre_gelu[i] = '0;
            act[i] = '0;
            for (j = 0; j < HIDDEN_SIZE; j = j + 1) begin
                w1[i][j] = in_w1[(i*HIDDEN_SIZE + j)*DATA_W +: DATA_W];
            end
        end

        for (k = 0; k < HIDDEN_SIZE; k = k + 1) begin
            for (i = 0; i < INTERMEDIATE_SIZE; i = i + 1) begin
                w2[k][i] = in_w2[(k*INTERMEDIATE_SIZE + i)*DATA_W +: DATA_W];
            end
        end

        for (i = 0; i < INTERMEDIATE_SIZE; i = i + 1) begin
            acc1 = b1[i];
            for (j = 0; j < HIDDEN_SIZE; j = j + 1) begin
                acc1 = acc1 + qmul_acc(w1[i][j], x[j]);
            end
            pre_gelu[i] = sat_data(acc1);
        end

        for (i = 0; i < INTERMEDIATE_SIZE; i = i + 1) begin
            xg = pre_gelu[i];
            x2 = qmul_acc(xg, xg);
            x3 = qmul_acc(x2, xg);
            u  = sat_data(xg + qmul_acc(c_acc, x3));
            v  = sat_data(qmul_acc(s_acc, u));
            v2 = qmul_acc(v, v);
            num = v * ((ACC_W'(27) * one_acc) + v2);
            den = (ACC_W'(27) * one_acc) + (ACC_W'(9) * v2);
            t = div_trunc_zero(num, den);
            if (t < -one_acc) t = -one_acc;
            if (t > one_acc) t = one_acc;
            g = one_acc + t;
            h = qmul_acc(xg, g);
            act[i] = sat_data(h >>> 1);
        end

        for (k = 0; k < HIDDEN_SIZE; k = k + 1) begin
            acc2 = b2[k];
            for (i = 0; i < INTERMEDIATE_SIZE; i = i + 1) begin
                acc2 = acc2 + qmul_acc(w2[k][i], act[i]);
            end
            y[k] = sat_data(acc2);
            out_hidden_states_next[k*DATA_W +: DATA_W] = y[k];
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy <= 1'b0;
            out_hidden_states <= '0;
        end else if (input_accepted) begin
            busy <= 1'b1;
            out_hidden_states <= out_hidden_states_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
