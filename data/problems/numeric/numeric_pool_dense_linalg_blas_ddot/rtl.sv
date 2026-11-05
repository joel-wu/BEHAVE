module dot_product5 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [15:0] in_dx,
    input  logic signed [15:0] in_dy,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [34:0] out_ddot
);

    logic [2:0] element_count;
    logic signed [34:0] running_sum;
    logic signed [34:0] out_ddot_reg;
    logic               out_pending;

    logic signed [31:0] product;
    logic signed [34:0] product_ext;
    logic signed [34:0] new_running_sum;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~out_pending;
    assign out_valid = out_pending;
    assign out_ddot  = out_ddot_reg;

    always_comb begin
        product = in_dx * in_dy;
        product_ext = {{3{product[31]}}, product};
        if (element_count == 3'd0) begin
            new_running_sum = product_ext;
        end else begin
            new_running_sum = running_sum + product_ext;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            element_count <= 3'd0;
            running_sum   <= 35'sd0;
            out_ddot_reg  <= 35'sd0;
            out_pending   <= 1'b0;
        end else begin
            if (output_consumed) begin
                out_pending <= 1'b0;
            end

            if (input_accepted) begin
                if (element_count == 3'd4) begin
                    out_ddot_reg  <= new_running_sum;
                    out_pending   <= 1'b1;
                    element_count <= 3'd0;
                    running_sum   <= 35'sd0;
                end else begin
                    element_count <= element_count + 3'd1;
                    running_sum   <= new_running_sum;
                end
            end
        end
    end

endmodule