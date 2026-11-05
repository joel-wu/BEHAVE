module data_width_converter (
    input logic clk, input logic reset,
    input logic [31:0] data_in, input logic data_valid,
    output logic [127:0] o_data_out, output logic o_data_out_valid
);
    logic [95:0] partial;
    logic [1:0] count;
    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            partial <= 0; count <= 0; o_data_out <= 0; o_data_out_valid <= 0;
        end else begin
            o_data_out_valid <= 0;
            if (data_valid) begin
                if (count == 3) begin
                    o_data_out <= {partial, data_in};
                    o_data_out_valid <= 1;
                    partial <= 0; count <= 0;
                end else begin
                    partial <= {partial[63:0], data_in};
                    count <= count + 1'b1;
                end
            end
        end
    end
endmodule
