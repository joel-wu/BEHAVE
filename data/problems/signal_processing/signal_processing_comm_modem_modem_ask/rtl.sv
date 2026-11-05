module signal_processing_comm_modem_modem_ask (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_demod,
    input  logic [2:0]  in_symbol,
    input  logic signed [15:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [2:0]  out_symbol,
    output logic signed [15:0] out_y,
    output logic signed [15:0] out_x_hat,
    output logic signed [15:0] out_r
);

    logic busy;

    logic [2:0]  next_symbol;
    logic signed [15:0] next_y;
    logic signed [15:0] next_x_hat;
    logic signed [15:0] next_r;

    logic [2:0] s_mod;
    logic [2:0] s_best;

    logic signed [16:0] x_ext;
    logic signed [16:0] a0;
    logic signed [16:0] a1;
    logic signed [16:0] a2;
    logic signed [16:0] a3;
    logic signed [16:0] a4;
    logic signed [16:0] a5;
    logic signed [16:0] a6;
    logic signed [16:0] a7;

    logic signed [16:0] d0;
    logic signed [16:0] d1;
    logic signed [16:0] d2;
    logic signed [16:0] d3;
    logic signed [16:0] d4;
    logic signed [16:0] d5;
    logic signed [16:0] d6;
    logic signed [16:0] d7;
    logic signed [16:0] best_d;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [2:0] gray_decode3(input logic [2:0] g);
        logic b2;
        logic b1;
        logic b0;
        begin
            b2 = g[2];
            b1 = b2 ^ g[1];
            b0 = b1 ^ g[0];
            gray_decode3 = {b2, b1, b0};
        end
    endfunction

    function automatic logic [2:0] gray_encode3(input logic [2:0] n);
        begin
            gray_encode3 = n ^ (n >> 1);
        end
    endfunction

    function automatic logic signed [16:0] amplitude17(input logic [2:0] s);
        begin
            unique case (s)
                3'd0: amplitude17 = -17'sd7;
                3'd1: amplitude17 = -17'sd5;
                3'd2: amplitude17 = -17'sd3;
                3'd3: amplitude17 = -17'sd1;
                3'd4: amplitude17 =  17'sd1;
                3'd5: amplitude17 =  17'sd3;
                3'd6: amplitude17 =  17'sd5;
                3'd7: amplitude17 =  17'sd7;
            endcase
        end
    endfunction

    function automatic logic signed [16:0] abs17(input logic signed [16:0] v);
        begin
            if (v < 17'sd0) abs17 = -v;
            else            abs17 = v;
        end
    endfunction

    always_comb begin
        next_symbol = 3'b000;
        next_y      = 16'sd0;
        next_x_hat  = 16'sd0;
        next_r      = 16'sd0;

        s_mod  = gray_decode3(in_symbol);
        s_best = 3'd0;
        x_ext  = {in_x[15], in_x};

        a0 = amplitude17(3'd0);
        a1 = amplitude17(3'd1);
        a2 = amplitude17(3'd2);
        a3 = amplitude17(3'd3);
        a4 = amplitude17(3'd4);
        a5 = amplitude17(3'd5);
        a6 = amplitude17(3'd6);
        a7 = amplitude17(3'd7);

        d0 = abs17(x_ext - a0);
        d1 = abs17(x_ext - a1);
        d2 = abs17(x_ext - a2);
        d3 = abs17(x_ext - a3);
        d4 = abs17(x_ext - a4);
        d5 = abs17(x_ext - a5);
        d6 = abs17(x_ext - a6);
        d7 = abs17(x_ext - a7);

        best_d = d0;
        s_best = 3'd0;
        if (d1 < best_d) begin
            best_d = d1;
            s_best = 3'd1;
        end
        if (d2 < best_d) begin
            best_d = d2;
            s_best = 3'd2;
        end
        if (d3 < best_d) begin
            best_d = d3;
            s_best = 3'd3;
        end
        if (d4 < best_d) begin
            best_d = d4;
            s_best = 3'd4;
        end
        if (d5 < best_d) begin
            best_d = d5;
            s_best = 3'd5;
        end
        if (d6 < best_d) begin
            best_d = d6;
            s_best = 3'd6;
        end
        if (d7 < best_d) begin
            best_d = d7;
            s_best = 3'd7;
        end

        if (in_demod == 1'b0) begin
            next_symbol = in_symbol;
            next_y      = 16'(amplitude17(s_mod));
            next_x_hat  = 16'(amplitude17(s_mod));
            next_r      = 16'sd0;
        end else begin
            next_symbol = gray_encode3(s_best);
            next_y      = 16'(amplitude17(s_best));
            next_x_hat  = 16'(amplitude17(s_best));
            next_r      = in_x;
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy       <= 1'b0;
            out_symbol <= 3'b000;
            out_y      <= 16'sd0;
            out_x_hat  <= 16'sd0;
            out_r      <= 16'sd0;
        end else if (input_accepted) begin
            busy       <= 1'b1;
            out_symbol <= next_symbol;
            out_y      <= next_y;
            out_x_hat  <= next_x_hat;
            out_r      <= next_r;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
