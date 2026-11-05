module accu (
    input logic clk,
    input logic rst_n,
    input logic [7:0] data_in,
    input logic valid_in,
    output logic valid_out,
    output logic [9:0] data_out
);
    logic [1:0] count;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count <= 0;
            valid_out <= 0;
            data_out <= 0;
        end else begin
            valid_out <= valid_in && count == 3;
            if (valid_in) begin
                count <= count + 1'b1;
                data_out <= count == 0 ? {2'b0, data_in}
                                      : data_out + {2'b0, data_in};
            end
        end
    end
endmodule
