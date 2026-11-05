module plane_rotation_vector_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_x_addr,
    input  logic [7:0]   in_y_addr,
    input  logic [7:0]   in_cs_addr,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_x_value,
    output logic [31:0]  out_y_value,

    output logic         x_req_valid,
    output logic         x_req_we,
    output logic [7:0]   x_req_addr,
    output logic [31:0]  x_req_wdata,
    input  logic         x_req_ready,
    input  logic         x_resp_valid,
    input  logic [31:0]  x_resp_rdata,

    output logic         y_req_valid,
    output logic         y_req_we,
    output logic [7:0]   y_req_addr,
    output logic [31:0]  y_req_wdata,
    input  logic         y_req_ready,
    input  logic         y_resp_valid,
    input  logic [31:0]  y_resp_rdata,

    output logic         c_req_valid,
    output logic [7:0]   c_req_addr,
    input  logic         c_req_ready,
    input  logic         c_resp_valid,
    input  logic [31:0]  c_resp_rdata,

    output logic         s_req_valid,
    output logic [7:0]   s_req_addr,
    input  logic         s_req_ready,
    input  logic         s_resp_valid,
    input  logic [31:0]  s_resp_rdata
);

    typedef enum logic [2:0] {
        ST_IDLE       = 3'd0,
        ST_REQ_READS  = 3'd1,
        ST_WAIT_RESPS = 3'd2,
        ST_REQ_WRITE  = 3'd3,
        ST_OUT        = 3'd4
    } state_t;

    state_t state;

    logic [7:0] x_addr_reg;
    logic [7:0] y_addr_reg;
    logic [7:0] cs_addr_reg;

    logic x_read_issued;
    logic y_read_issued;
    logic c_read_issued;
    logic s_read_issued;

    logic x_read_done;
    logic y_read_done;
    logic c_read_done;
    logic s_read_done;

    logic signed [31:0] x_old_reg;
    logic signed [31:0] y_old_reg;
    logic signed [31:0] c_val_reg;
    logic signed [31:0] s_val_reg;

    logic x_write_issued;
    logic y_write_issued;

    logic signed [63:0] prod_cx;
    logic signed [63:0] prod_sy;
    logic signed [63:0] prod_cy;
    logic signed [63:0] prod_sx;
    logic signed [63:0] x_rot_full;
    logic signed [63:0] y_rot_full;
    logic [31:0]        x_new_bits;
    logic [31:0]        y_new_bits;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready    = (state == ST_IDLE);
    assign out_valid   = (state == ST_OUT);
    assign out_x_value = x_new_bits;
    assign out_y_value = y_new_bits;

    always_comb begin
        prod_cx    = c_val_reg * x_old_reg;
        prod_sy    = s_val_reg * y_old_reg;
        prod_cy    = c_val_reg * y_old_reg;
        prod_sx    = s_val_reg * x_old_reg;
        x_rot_full = prod_cx + prod_sy;
        y_rot_full = prod_cy - prod_sx;
        x_new_bits = x_rot_full[31:0];
        y_new_bits = y_rot_full[31:0];
    end

    always_comb begin
        x_req_valid = 1'b0;
        x_req_we    = 1'b0;
        x_req_addr  = 8'h00;
        x_req_wdata = 32'h00000000;

        y_req_valid = 1'b0;
        y_req_we    = 1'b0;
        y_req_addr  = 8'h00;
        y_req_wdata = 32'h00000000;

        c_req_valid = 1'b0;
        c_req_addr  = 8'h00;

        s_req_valid = 1'b0;
        s_req_addr  = 8'h00;

        case (state)
            ST_REQ_READS: begin
                x_req_valid = ~x_read_issued;
                x_req_we    = 1'b0;
                x_req_addr  = x_addr_reg;

                y_req_valid = ~y_read_issued;
                y_req_we    = 1'b0;
                y_req_addr  = y_addr_reg;

                c_req_valid = ~c_read_issued;
                c_req_addr  = cs_addr_reg;

                s_req_valid = ~s_read_issued;
                s_req_addr  = cs_addr_reg;
            end

            ST_REQ_WRITE: begin
                x_req_valid = ~x_write_issued;
                x_req_we    = 1'b1;
                x_req_addr  = x_addr_reg;
                x_req_wdata = x_new_bits;

                y_req_valid = ~y_write_issued;
                y_req_we    = 1'b1;
                y_req_addr  = y_addr_reg;
                y_req_wdata = y_new_bits;
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= ST_IDLE;

            x_addr_reg <= 8'h00;
            y_addr_reg <= 8'h00;
            cs_addr_reg <= 8'h00;

            x_read_issued <= 1'b0;
            y_read_issued <= 1'b0;
            c_read_issued <= 1'b0;
            s_read_issued <= 1'b0;

            x_read_done <= 1'b0;
            y_read_done <= 1'b0;
            c_read_done <= 1'b0;
            s_read_done <= 1'b0;

            x_old_reg <= 32'sh00000000;
            y_old_reg <= 32'sh00000000;
            c_val_reg <= 32'sh00000000;
            s_val_reg <= 32'sh00000000;

            x_write_issued <= 1'b0;
            y_write_issued <= 1'b0;
        end else begin
            // Endpoints are independent: a response may arrive while another
            // endpoint is still waiting to issue its read request.
            if ((state == ST_REQ_READS) || (state == ST_WAIT_RESPS)) begin
                if ((!x_read_done) && x_resp_valid) begin
                    x_old_reg <= $signed(x_resp_rdata);
                    x_read_done <= 1'b1;
                end
                if ((!y_read_done) && y_resp_valid) begin
                    y_old_reg <= $signed(y_resp_rdata);
                    y_read_done <= 1'b1;
                end
                if ((!c_read_done) && c_resp_valid) begin
                    c_val_reg <= $signed(c_resp_rdata);
                    c_read_done <= 1'b1;
                end
                if ((!s_read_done) && s_resp_valid) begin
                    s_val_reg <= $signed(s_resp_rdata);
                    s_read_done <= 1'b1;
                end
            end
            case (state)
                ST_IDLE: begin
                    if (input_accepted) begin
                        x_addr_reg <= in_x_addr;
                        y_addr_reg <= in_y_addr;
                        cs_addr_reg <= in_cs_addr;

                        x_read_issued <= 1'b0;
                        y_read_issued <= 1'b0;
                        c_read_issued <= 1'b0;
                        s_read_issued <= 1'b0;

                        x_read_done <= 1'b0;
                        y_read_done <= 1'b0;
                        c_read_done <= 1'b0;
                        s_read_done <= 1'b0;

                        x_write_issued <= 1'b0;
                        y_write_issued <= 1'b0;

                        state <= ST_REQ_READS;
                    end
                end

                ST_REQ_READS: begin
                    if ((!x_read_issued) && x_req_ready) begin
                        x_read_issued <= 1'b1;
                    end
                    if ((!y_read_issued) && y_req_ready) begin
                        y_read_issued <= 1'b1;
                    end
                    if ((!c_read_issued) && c_req_ready) begin
                        c_read_issued <= 1'b1;
                    end
                    if ((!s_read_issued) && s_req_ready) begin
                        s_read_issued <= 1'b1;
                    end

                    if ((x_read_issued || x_req_ready) &&
                        (y_read_issued || y_req_ready) &&
                        (c_read_issued || c_req_ready) &&
                        (s_read_issued || s_req_ready)) begin
                        state <= ST_WAIT_RESPS;
                    end
                end

                ST_WAIT_RESPS: begin
                    if ((x_read_done || x_resp_valid) &&
                        (y_read_done || y_resp_valid) &&
                        (c_read_done || c_resp_valid) &&
                        (s_read_done || s_resp_valid)) begin
                        x_write_issued <= 1'b0;
                        y_write_issued <= 1'b0;
                        state <= ST_REQ_WRITE;
                    end
                end

                ST_REQ_WRITE: begin
                    if ((!x_write_issued) && x_req_ready) begin
                        x_write_issued <= 1'b1;
                    end
                    if ((!y_write_issued) && y_req_ready) begin
                        y_write_issued <= 1'b1;
                    end

                    if ((x_write_issued || x_req_ready) &&
                        (y_write_issued || y_req_ready)) begin
                        state <= ST_OUT;
                    end
                end

                ST_OUT: begin
                    if (output_consumed) begin
                        state <= ST_IDLE;
                    end
                end
            endcase
        end
    end

endmodule
