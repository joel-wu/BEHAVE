module signal_processing_comm_modem_modem_bpsk (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_op,
    input  logic [0:0]  in_sym,
    input  logic [31:0] in_x_real,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [0:0]  out_sym,
    output logic [31:0] out_y_real,
    output logic [31:0] out_x_hat_real,
    output logic [31:0] out_r_real
);

logic        busy;
logic [31:0] x_hat_real_state;
logic [31:0] r_real_state;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy            <= 1'b0;
        x_hat_real_state <= 32'h00000000;
        r_real_state     <= 32'h00000000;
        out_sym         <= 1'b0;
        out_y_real      <= 32'h00000000;
        out_x_hat_real  <= 32'h00000000;
        out_r_real      <= 32'h00000000;
    end else if (input_accepted) begin
        busy <= 1'b1;

        if (in_op) begin
            if ($signed(in_x_real) > 32'sd0) begin
                out_sym         <= 1'b0;
                out_y_real      <= 32'h00000001;
                out_x_hat_real  <= 32'h00000001;
                out_r_real      <= in_x_real;

                x_hat_real_state <= 32'h00000001;
                r_real_state     <= in_x_real;
            end else begin
                out_sym         <= 1'b1;
                out_y_real      <= 32'hFFFFFFFF;
                out_x_hat_real  <= 32'hFFFFFFFF;
                out_r_real      <= in_x_real;

                x_hat_real_state <= 32'hFFFFFFFF;
                r_real_state     <= in_x_real;
            end
        end else begin
            out_sym        <= 1'b0;
            out_y_real     <= in_sym[0] ? 32'hFFFFFFFF : 32'h00000001;
            out_x_hat_real <= x_hat_real_state;
            out_r_real     <= r_real_state;

            x_hat_real_state <= x_hat_real_state;
            r_real_state     <= r_real_state;
        end
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule