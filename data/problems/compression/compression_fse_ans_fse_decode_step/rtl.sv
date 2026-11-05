module compression_fse_ans_fse_decode_step (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [1:0] in_op,
    input  logic [3:0] in_table_log,
    input  logic [15:0] in_state,
    input  logic [31:0] in_bits,
    input  logic [11:0] in_table_index,
    input  logic [7:0] in_symbol,
    input  logic [4:0] in_nb_bits,
    input  logic [15:0] in_new_state,
    output logic out_valid,
    input  logic out_ready,
    output logic [7:0] out_symbol,
    output logic [15:0] out_next_state,
    output logic [4:0] out_bits_consumed,
    output logic out_error
);
    logic [7:0] symbols [0:4095];
    logic [4:0] counts [0:4095];
    logic [15:0] bases [0:4095];
    logic [4095:0] valid_entries;
    logic [16:0] size, successor;
    logic [4:0] count;
    logic [7:0] symbol;
    logic good;

    always_comb begin
        size = 17'd1 << in_table_log;
        successor = 0;
        count = 0;
        symbol = 0;
        good = 0;
        if (in_op == 0 && in_table_log >= 5 && in_table_log <= 12 &&
            {1'b0, in_state} < size && valid_entries[in_state[11:0]]) begin
            count = counts[in_state[11:0]];
            symbol = symbols[in_state[11:0]];
            if (count <= {1'b0, in_table_log}) begin
                successor = {1'b0, bases[in_state[11:0]]}
                            + (in_bits[16:0] & ((17'd1 << count) - 17'd1));
                good = successor < size;
            end
        end
    end

    assign in_ready = !out_valid || out_ready;

    always_ff @(posedge clk) begin
        if (rst) begin
            valid_entries <= '0;
            out_valid <= 0;
            out_symbol <= 0;
            out_next_state <= 0;
            out_bits_consumed <= 0;
            out_error <= 0;
        end else if (in_ready) begin
            out_valid <= 0;
            if (in_valid) begin
                case (in_op)
                    2'd1: begin
                        symbols[in_table_index] <= in_symbol;
                        counts[in_table_index] <= in_nb_bits;
                        bases[in_table_index] <= in_new_state;
                        valid_entries[in_table_index] <= 1;
                    end
                    2'd2: valid_entries <= '0;
                    default: begin
                        out_valid <= 1;
                        out_symbol <= good ? symbol : 8'd0;
                        out_next_state <= good ? successor[15:0] : 16'd0;
                        out_bits_consumed <= good ? count : 5'd0;
                        out_error <= !good;
                    end
                endcase
            end
        end
    end
endmodule
