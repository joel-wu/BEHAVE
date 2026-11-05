module buffer_bank_line_buffer_circular_fifo #(
    parameter int N = 16,
    parameter int W = 32,
    parameter int ALIGNED_TO_BYTES = 4,
    parameter bit SINGLE_PORTED = 1'b0
) (
    input  logic                 clk,
    input  logic                 rst,

    input  logic                 in_valid,
    output logic                 in_ready,
    input  logic                 in_write,
    input  logic [3:0]           in_addr,
    input  logic                 in_fromDMA,
    input  logic                 in_mask,
    input  logic [31:0]          in_data,

    output logic                 out_valid,
    input  logic                 out_ready,
    output logic [31:0]          out_data,
    output logic                 out_fromDMA
);

    logic [31:0] mem [0:15];
    logic        busy;

    wire input_accepted;
    wire output_consumed;

    assign output_consumed = out_valid & out_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;
    assign input_accepted  = in_valid & in_ready;

    always_ff @(posedge clk) begin
        if (rst) begin
            integer i;
            for (i = 0; i < 16; i = i + 1) begin
                mem[i] <= 32'd0;
            end
            busy        <= 1'b0;
            out_data    <= 32'd0;
            out_fromDMA <= 1'b0;
        end else begin
            if (input_accepted) begin
                if (in_write) begin
                    if (in_mask) mem[in_addr] <= in_data;
                    busy         <= 1'b0;
                end else begin
                    out_data     <= mem[in_addr];
                    out_fromDMA  <= in_fromDMA;
                    busy         <= 1'b1;
                end
            end else if (output_consumed) begin
                busy <= 1'b0;
            end
        end
    end

endmodule
