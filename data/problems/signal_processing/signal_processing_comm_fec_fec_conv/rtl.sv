module signal_processing_comm_fec_fec_conv (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_data,
    input  logic       in_last,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);
    logic [6:0] history;
    logic [31:0] pending;
    logic [2:0] remaining;
    logic [6:0] next_history;
    logic [31:0] encoded;
    logic bit_in;

    assign out_valid = (remaining != 0);
    assign out_data = pending[31:24];
    assign in_ready = (remaining == 0) || (remaining == 1 && out_ready);

    always_comb begin
        next_history = history;
        encoded = 0;
        bit_in = 0;
        for (integer j = 0; j < 14; j = j+1) begin
            if (j < 8 || in_last) begin
                bit_in = (j < 8) ? in_data[7-j] : 1'b0;
                next_history = {next_history[5:0], bit_in};
                encoded = {encoded[29:0], ^(next_history & 7'h6D),
                                         ^(next_history & 7'h4F)};
            end
        end
        // Align the first byte at the head of the output queue.
        encoded = in_last ? (encoded << 4) : (encoded << 16);
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            history <= 0;
            pending <= 0;
            remaining <= 0;
        end else if (in_valid && in_ready) begin
            history <= in_last ? 7'd0 : next_history;
            pending <= encoded;
            remaining <= in_last ? 3'd4 : 3'd2;
        end else if (out_valid && out_ready) begin
            pending <= pending << 8;
            remaining <= remaining - 1'b1;
        end
    end
endmodule
