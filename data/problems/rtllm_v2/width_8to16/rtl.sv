module width_8to16 (
    input logic clk,
    input logic rst_n,
    input logic valid_in,
    input logic [7:0] data_in,
    output logic valid_out,
    output logic [15:0] data_out
);
    logic [7:0] pending_byte;
    logic have_pending;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pending_byte <= 8'b0;
            have_pending <= 1'b0;
            valid_out <= 1'b0;
            data_out <= 16'b0;
        end else begin
            valid_out <= 1'b0;
            if (valid_in) begin
                have_pending <= ~have_pending;
                if (have_pending) begin
                    data_out <= {pending_byte, data_in};
                    valid_out <= 1'b1;
                end else begin
                    pending_byte <= data_in;
                end
            end
        end
    end
endmodule
