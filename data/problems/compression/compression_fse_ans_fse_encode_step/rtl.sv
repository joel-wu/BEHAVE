module compression_fse_ans_fse_encode_step (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [1:0] in_op,
    input  logic [3:0] in_table_log,
    input  logic [15:0] in_state,
    input  logic [7:0] in_symbol,
    input  logic [31:0] in_delta_nb_bits,
    input  logic signed [31:0] in_delta_find_state,
    input  logic [11:0] in_table_index,
    input  logic [15:0] in_next_state,
    output logic out_valid,
    input  logic out_ready,
    output logic [4:0] out_nb_bits,
    output logic [15:0] out_bits,
    output logic [15:0] out_next_state,
    output logic out_error
);
    logic [31:0] delta_bits [0:255];
    logic signed [31:0] delta_find [0:255];
    logic [255:0] symbol_valid;
    logic [15:0] states [0:4095];
    logic [4095:0] state_valid;

    logic [16:0] size;
    logic [32:0] sum;
    logic [16:0] count;
    logic signed [32:0] index;
    logic [15:0] successor, bits;
    logic good;

    always_comb begin
        size = 17'd1 << in_table_log;
        sum = {17'd0, in_state} + {1'b0, delta_bits[in_symbol]};
        count = sum[32:16];
        index = $signed({17'd0, in_state} >> count)
                + $signed({delta_find[in_symbol][31], delta_find[in_symbol]});
        successor = 0;
        bits = 0;
        good = 0;
        if (in_table_log >= 5 && in_table_log <= 12 &&
            {1'b0, in_state} >= size && {1'b0, in_state} < (size << 1) &&
            symbol_valid[in_symbol] && count <= {13'd0, in_table_log} &&
            index >= 0 && index < $signed({16'd0, size})) begin
            if (state_valid[index[11:0]]) begin
                successor = states[index[11:0]];
                if ({1'b0, successor} >= size && {1'b0, successor} < (size << 1)) begin
                    good = 1;
                    bits = in_state & ((16'd1 << count) - 16'd1);
                end
            end
        end
    end

    assign in_ready = !out_valid || out_ready;

    always_ff @(posedge clk) begin
        if (rst) begin
            symbol_valid <= '0;
            state_valid <= '0;
            out_valid <= 0;
            out_nb_bits <= 0;
            out_bits <= 0;
            out_next_state <= 0;
            out_error <= 0;
        end else if (in_ready) begin
            out_valid <= 0;
            if (in_valid) begin
                case (in_op)
                    2'd1: begin
                        delta_bits[in_symbol] <= in_delta_nb_bits;
                        delta_find[in_symbol] <= in_delta_find_state;
                        symbol_valid[in_symbol] <= 1;
                    end
                    2'd2: begin
                        states[in_table_index] <= in_next_state;
                        state_valid[in_table_index] <= 1;
                    end
                    2'd3: begin
                        symbol_valid <= '0;
                        state_valid <= '0;
                    end
                    default: begin
                        out_valid <= 1;
                        out_nb_bits <= good ? count[4:0] : 5'd0;
                        out_bits <= good ? bits : 16'd0;
                        out_next_state <= good ? successor : 16'd0;
                        out_error <= !good;
                    end
                endcase
            end
        end
    end
endmodule
