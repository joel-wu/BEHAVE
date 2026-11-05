module little_endian_u32_reader (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [7:0] in_byte,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_word
);
    logic [1:0] count;
    logic [31:0] partial_word;
    logic pending;
    assign in_ready = !pending || out_ready;
    assign out_valid = pending && !rst;
    always_ff @(posedge clk) begin
        if (rst) begin
            count <= 0;
            partial_word <= 0;
            out_word <= 0;
            pending <= 0;
        end else begin
            if (out_valid && out_ready) pending <= 0;
            if (in_valid && in_ready) begin
                if (count == 3) begin
                    out_word <= partial_word | {in_byte,24'd0};
                    pending <= 1;
                    partial_word <= 0;
                    count <= 0;
                end else begin
                    partial_word <= partial_word | (32'(in_byte) << (8*count));
                    count <= count+1'b1;
                end
            end
        end
    end
endmodule
