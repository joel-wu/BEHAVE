module signal_processing_comm_modem_modem_pi4dqpsk (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_mode,
    input  logic [1:0]  in_sym,
    input  logic [31:0] in_i,
    input  logic [31:0] in_q,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [1:0]  out_sym,
    output logic [31:0] out_i,
    output logic [31:0] out_q
);

    logic pending;
    logic signed [31:0] anchor_i, anchor_q;
    logic [2:0] phase;
    logic negative_pi;

    logic [2:0] next_phase;
    logic next_negative_pi;
    logic signed [31:0] next_anchor_i, next_anchor_q;
    logic [1:0] result_symbol;
    logic [31:0] result_i, result_q;
    logic signed [33:0] x, y, previous_i, previous_q;
    logic signed [66:0] dot, determinant;
    logic [65:0] norm2;

    assign in_ready = !pending || out_ready;
    assign out_valid = pending;

    function automatic logic signed [33:0] rotate_i(
        input logic signed [31:0] a, b, input logic [2:0] p
    );
        logic signed [33:0] ax, by;
        begin
            ax = 34'(a);
            by = 34'(b);
            case (p)
                0: rotate_i = ax;
                1: rotate_i = ax - by;
                2: rotate_i = -by;
                3: rotate_i = -ax - by;
                4: rotate_i = -ax;
                5: rotate_i = by - ax;
                6: rotate_i = by;
                7: rotate_i = ax + by;
            endcase
        end
    endfunction

    function automatic logic signed [33:0] rotate_q(
        input logic signed [31:0] a, b, input logic [2:0] p
    );
        logic signed [33:0] ax, by;
        begin
            ax = 34'(a);
            by = 34'(b);
            case (p)
                0: rotate_q = by;
                1: rotate_q = ax + by;
                2: rotate_q = ax;
                3: rotate_q = ax - by;
                4: rotate_q = -by;
                5: rotate_q = -ax - by;
                6: rotate_q = -ax;
                7: rotate_q = by - ax;
            endcase
        end
    endfunction

    function automatic logic [31:0] normalize(
        input logic signed [33:0] v, input logic [65:0] squared_norm
    );
        logic [33:0] magnitude;
        logic [127:0] numerator, trial_squared, threshold;
        logic [31:0] q, trial;
        logic [32:0] midpoint;
        begin
            magnitude = v < 0 ? $unsigned(-v) : $unsigned(v);
            numerator = (128'(magnitude) * 128'(magnitude)) << 60;
            q = 0;
            for (int bit_index = 30; bit_index >= 0; bit_index--) begin
                trial = q | (32'd1 << bit_index);
                trial_squared = 128'(trial) * 128'(trial) * 128'(squared_norm);
                if (trial_squared <= numerator)
                    q = trial;
            end
            midpoint = (33'(q) << 1) + 33'd1;
            threshold = 128'(midpoint) * 128'(midpoint) * 128'(squared_norm);
            if ((numerator << 2) >= threshold)
                q = q + 32'd1;
            normalize = v < 0 ? -q : q;
        end
    endfunction

    always_comb begin
        next_phase = phase;
        next_anchor_i = anchor_i;
        next_anchor_q = anchor_q;
        next_negative_pi = negative_pi;
        result_symbol = 0;
        result_i = 0;
        result_q = 0;
        previous_i = rotate_i(anchor_i, anchor_q, phase);
        previous_q = rotate_q(anchor_i, anchor_q, phase);
        x = 34'($signed(in_i));
        y = 34'($signed(in_q));
        dot = 0;
        determinant = 0;
        norm2 = 0;
        if (!in_mode) begin
            case (in_sym)
                0: next_phase = phase + 3'd1;
                1: next_phase = phase + 3'd3;
                2: next_phase = phase - 3'd1;
                3: next_phase = phase - 3'd3;
            endcase
            x = rotate_i(anchor_i, anchor_q, next_phase);
            y = rotate_q(anchor_i, anchor_q, next_phase);
            norm2 = 66'(x) * 66'(x) + 66'(y) * 66'(y);
            result_i = normalize(x,norm2);
            result_q = normalize(y,norm2);
            next_negative_pi = (y == 0 && x < 0 && in_sym[1]);
        end else begin
            if (x == 0 && y == 0)
                x = 1;
            dot = 67'(x) * 67'(previous_i) + 67'(y) * 67'(previous_q);
            determinant = 67'(y) * 67'(previous_i) - 67'(x) * 67'(previous_q);
            if (determinant > 0)
                result_symbol = dot < 0 ? 2'd1 : 2'd0;
            else if (determinant < 0)
                result_symbol = dot < 0 ? 2'd3 : 2'd2;
            else if (dot < 0)
                result_symbol = (negative_pi || y > 0 || (y == 0 && x < 0)) ? 2'd1 : 2'd3;
            else
                result_symbol = 2'd2;
            next_anchor_i = 32'(x);
            next_anchor_q = 32'(y);
            next_phase = 0;
            next_negative_pi = 0;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            pending <= 0;
            anchor_i <= 1;
            anchor_q <= 0;
            phase <= 0;
            negative_pi <= 0;
            out_sym <= 0;
            out_i <= 0;
            out_q <= 0;
        end else if (in_valid && in_ready) begin
            pending <= 1;
            anchor_i <= next_anchor_i;
            anchor_q <= next_anchor_q;
            phase <= next_phase;
            negative_pi <= next_negative_pi;
            out_sym <= result_symbol;
            out_i <= result_i;
            out_q <= result_q;
        end else if (out_ready) begin
            pending <= 0;
        end
    end
endmodule
