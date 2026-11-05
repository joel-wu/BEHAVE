module cvdp_copilot_apb_gpio #(parameter int GPIO_WIDTH=8)(
    input logic pclk, preset_n, psel,
    input logic [7:2] paddr,
    input logic penable, pwrite,
    input logic [31:0] pwdata,
    input logic [GPIO_WIDTH-1:0] gpio_in,
    output logic [31:0] prdata,
    output logic pready, pslverr,
    output logic [GPIO_WIDTH-1:0] gpio_out, gpio_enable, gpio_int,
    output logic comb_int
);
    logic [GPIO_WIDTH-1:0] reg_out, reg_enable, irq_enable, irq_type, irq_polarity;
    logic [GPIO_WIDTH-1:0] sync1, sync2, history;
    logic [GPIO_WIDTH-1:0] rising, falling, edge_hits, level_hits;
    assign pready=preset_n;
    assign pslverr=0;
    assign gpio_out=reg_out;
    assign gpio_enable=reg_enable;
    assign rising=sync2 & ~history;
    assign falling=~sync2 & history;
    assign edge_hits=(rising & irq_polarity) | (falling & ~irq_polarity);
    assign level_hits=(sync2 & irq_polarity) | (~sync2 & ~irq_polarity);
    assign gpio_int=irq_enable & ((irq_type & edge_hits) | (~irq_type & level_hits));
    assign comb_int=|gpio_int;
    always_comb begin
        prdata=0;
        if(preset_n && psel && penable && !pwrite) case(paddr)
            0: prdata=32'(sync2);
            1: prdata=32'(reg_out);
            2: prdata=32'(reg_enable);
            3: prdata=32'(irq_enable);
            4: prdata=32'(irq_type);
            5: prdata=32'(irq_polarity);
            6: prdata=32'(gpio_int);
            default: prdata=0;
        endcase
    end
    always_ff @(posedge pclk or negedge preset_n) begin
        if(!preset_n) begin
            reg_out<=0; reg_enable<=0; irq_enable<=0; irq_type<=0; irq_polarity<=0;
            sync1<=0; sync2<=0; history<=0;
        end else begin
            sync1<=gpio_in; sync2<=sync1; history<=sync2;
            if(psel && penable && pwrite) case(paddr)
                1: reg_out<=pwdata[GPIO_WIDTH-1:0];
                2: reg_enable<=pwdata[GPIO_WIDTH-1:0];
                3: irq_enable<=pwdata[GPIO_WIDTH-1:0];
                4: irq_type<=pwdata[GPIO_WIDTH-1:0];
                5: irq_polarity<=pwdata[GPIO_WIDTH-1:0];
                default: ;
            endcase
        end
    end
endmodule
